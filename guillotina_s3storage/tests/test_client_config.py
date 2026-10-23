import asyncio
import logging
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import botocore.exceptions
import pytest
import pytest_asyncio
from aiohttp import web

from guillotina_s3storage import storage
from guillotina_s3storage.storage import DEFAULT_DOWNLOAD_MAX_RESUMES
from guillotina_s3storage.storage import DEFAULT_DOWNLOAD_TIMEOUT
from guillotina_s3storage.storage import DEFAULT_MAX_POOL_CONNECTIONS
from guillotina_s3storage.storage import S3BlobStore
from guillotina_s3storage.storage import S3FileStorageManager

BASE_SETTINGS = {
    "aws_client_id": "x",
    "aws_client_secret": "x",
    "bucket": "testbucket",
}
DATA = bytes(range(256)) * 16


class FakeS3:
    def __init__(self):
        self.data = DATA
        self.etag = '"v1"'
        self.stall_after = []
        self.requests = []
        self.release = asyncio.Event()

    async def handle(self, request):
        self.requests.append(dict(request.headers))
        if_match = request.headers.get("If-Match")
        if if_match is not None and if_match != self.etag:
            return web.Response(
                status=412,
                content_type="application/xml",
                text="<Error><Code>PreconditionFailed</Code>"
                "<Message>At least one of the pre-conditions you specified "
                "did not hold</Message></Error>",
            )

        body = self.data
        status = 200
        headers = {"ETag": self.etag}
        range_header = request.headers.get("Range")
        if range_header:
            first, _, last = range_header[len("bytes=") :].partition("-")
            last = int(last) if last else len(self.data) - 1
            body = self.data[int(first) : last + 1]
            status = 206
            headers["Content-Range"] = f"bytes {first}-{last}/{len(self.data)}"

        stall_after = self.stall_after.pop(0) if self.stall_after else None
        resp = web.StreamResponse(status=status, headers=headers)
        resp.content_length = len(body)
        await resp.prepare(request)
        if stall_after is None:
            await resp.write(body)
            return resp
        await resp.write(body[:stall_after])
        await self.release.wait()
        return resp


@pytest_asyncio.fixture
async def fake_s3():
    server = FakeS3()
    app = web.Application()
    app.router.add_get("/{tail:.*}", server.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    yield server
    server.release.set()
    await runner.cleanup()


@pytest_asyncio.fixture
async def make_store(fake_s3, monkeypatch):
    stores = []

    async def _make(**settings):
        defaults = dict(
            BASE_SETTINGS,
            endpoint_url=fake_s3.url,
            ssl=False,
            region_name="us-east-1",
            read_timeout=0.5,
            max_attempts=1,
        )
        store = S3BlobStore(dict(defaults, **settings))
        await store.initialize()
        store.get_bucket_name = AsyncMock(return_value="testbucket")
        monkeypatch.setattr(storage, "get_utility", lambda iface: store)
        stores.append(store)
        return store

    yield _make
    for store in stores:
        await store.finalize()


def _manager():
    return S3FileStorageManager(MagicMock(), MagicMock(), MagicMock())


async def _read(iterator):
    data = b""
    async for chunk in iterator:
        data += chunk
    return data


def _s3_logs(caplog, level):
    return [
        r
        for r in caplog.records
        if r.name == "guillotina_s3storage" and r.levelno == level
    ]


def test_client_config_defaults():
    util = S3BlobStore(dict(BASE_SETTINGS))
    config = util._opts["config"]
    assert config.max_pool_connections == DEFAULT_MAX_POOL_CONNECTIONS
    assert config.connect_timeout == 60
    assert config.read_timeout == 60
    assert config.retries is None
    assert util.download_timeout == DEFAULT_DOWNLOAD_TIMEOUT
    assert util.download_max_resumes == DEFAULT_DOWNLOAD_MAX_RESUMES


def test_client_config_from_string_settings():
    settings = dict(
        BASE_SETTINGS,
        max_pool_connections="60",
        connect_timeout="3",
        read_timeout="7.5",
        max_attempts="1",
        download_timeout="20",
        download_max_resumes="5",
    )
    util = S3BlobStore(settings)
    config = util._opts["config"]
    assert config.max_pool_connections == 60
    assert config.connect_timeout == 3.0
    assert config.read_timeout == 7.5
    assert config.retries == {"total_max_attempts": 1}
    assert util._s3_request_semaphore._value == 60
    assert util.download_timeout == 20.0
    assert util.download_max_resumes == 5


def test_client_config_unset_values_use_defaults():
    settings = dict(
        BASE_SETTINGS,
        max_pool_connections=None,
        connect_timeout=None,
        read_timeout="",
        max_attempts=None,
        download_timeout=None,
    )
    util = S3BlobStore(settings)
    config = util._opts["config"]
    assert config.max_pool_connections == DEFAULT_MAX_POOL_CONNECTIONS
    assert config.connect_timeout == 60
    assert config.read_timeout == 60
    assert util.download_timeout == DEFAULT_DOWNLOAD_TIMEOUT


@pytest.mark.asyncio
async def test_download_without_stall(fake_s3, make_store):
    await make_store()
    assert await _read(_manager().iter_data(uri="key")) == DATA
    assert "Range" not in fake_s3.requests[0]


@pytest.mark.asyncio
async def test_empty_file_is_read_without_range(fake_s3, make_store):
    fake_s3.data = b""
    await make_store()
    assert await _read(_manager().iter_data(uri="key")) == b""
    assert "Range" not in fake_s3.requests[0]


@pytest.mark.asyncio
async def test_stall_resumes_from_offset(fake_s3, make_store, caplog):
    fake_s3.stall_after = [1000]
    await make_store()

    with caplog.at_level(logging.WARNING, logger="guillotina_s3storage"):
        assert await _read(_manager().iter_data(uri="key")) == DATA

    assert len(fake_s3.requests) == 2
    assert fake_s3.requests[1]["Range"] == "bytes=1000-"
    assert fake_s3.requests[1]["If-Match"] == fake_s3.etag
    assert len(_s3_logs(caplog, logging.WARNING)) == 1
    assert _s3_logs(caplog, logging.ERROR) == []


@pytest.mark.asyncio
async def test_range_resume_keeps_end(fake_s3, make_store, monkeypatch):
    fake_s3.stall_after = [500]
    await make_store()
    monkeypatch.setattr(storage, "_is_uploaded_file", lambda file: True)
    mng = _manager()
    mng.field.query.return_value = MagicMock(uri="key")

    data = await _read(mng.read_range(100, 2100))

    assert data == DATA[100:2100]
    assert fake_s3.requests[0]["Range"] == "bytes=100-2099"
    assert fake_s3.requests[1]["Range"] == "bytes=600-2099"


@pytest.mark.asyncio
async def test_resume_count_resets_on_progress(fake_s3, make_store):
    fake_s3.stall_after = [100, 100, 100, 100]
    await make_store(download_max_resumes=1)

    assert await _read(_manager().iter_data(uri="key")) == DATA
    assert len(fake_s3.requests) == 5


@pytest.mark.asyncio
async def test_repeated_stalls_fail_after_max_resumes(fake_s3, make_store, caplog):
    fake_s3.stall_after = [0, 0, 0, 0]
    await make_store(download_max_resumes=2)

    with caplog.at_level(logging.WARNING, logger="guillotina_s3storage"):
        with pytest.raises(asyncio.TimeoutError):
            await _read(_manager().iter_data(uri="key"))

    assert len(fake_s3.requests) == 3
    assert len(_s3_logs(caplog, logging.WARNING)) == 2
    errors = _s3_logs(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "key" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_changed_object_fails_on_412(fake_s3, make_store, caplog):
    fake_s3.stall_after = [1000]
    await make_store()
    received = b""

    with caplog.at_level(logging.ERROR, logger="guillotina_s3storage"):
        with pytest.raises(botocore.exceptions.ClientError) as exc_info:
            async for chunk in _manager().iter_data(uri="key"):
                received += chunk
                fake_s3.etag = '"v2"'

    assert exc_info.value.response["Error"]["Code"] == "PreconditionFailed"
    assert received == DATA[:1000]
    assert len(fake_s3.requests) == 2
    assert len(_s3_logs(caplog, logging.ERROR)) == 1


@pytest.mark.asyncio
async def test_client_error_is_not_retried(fake_s3, make_store):
    fake_s3.etag = '"v2"'
    await make_store()

    with pytest.raises(botocore.exceptions.ClientError):
        await _read(_manager().iter_data(uri="key", IfMatch='"v1"'))
    assert len(fake_s3.requests) == 1


@pytest.mark.asyncio
async def test_pool_wait_is_bounded(fake_s3, make_store, caplog):
    fake_s3.stall_after = [1000]
    await make_store(max_pool_connections=1, download_timeout=0.5, read_timeout=30)

    first = _manager().iter_data(uri="first")
    await first.__anext__()

    started = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="guillotina_s3storage"):
        with pytest.raises(asyncio.TimeoutError):
            await _read(_manager().iter_data(uri="second"))

    assert time.monotonic() - started < 2
    assert len(fake_s3.requests) == 1
    assert "second" in _s3_logs(caplog, logging.ERROR)[0].getMessage()
    await first.aclose()
