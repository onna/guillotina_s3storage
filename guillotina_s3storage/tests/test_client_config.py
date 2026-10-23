import asyncio
import logging
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import botocore.exceptions
import pytest
from aiohttp import web

from guillotina_s3storage import storage
from guillotina_s3storage.storage import DEFAULT_CONNECT_TIMEOUT
from guillotina_s3storage.storage import DEFAULT_MAX_ATTEMPTS
from guillotina_s3storage.storage import DEFAULT_MAX_POOL_CONNECTIONS
from guillotina_s3storage.storage import DEFAULT_READ_TIMEOUT
from guillotina_s3storage.storage import S3BlobStore
from guillotina_s3storage.storage import S3FileStorageManager

BASE_SETTINGS = {
    "aws_client_id": "x",
    "aws_client_secret": "x",
    "bucket": "testbucket",
}


def test_client_config_defaults():
    util = S3BlobStore(dict(BASE_SETTINGS))
    config = util._opts["config"]
    assert config.max_pool_connections == DEFAULT_MAX_POOL_CONNECTIONS
    assert config.connect_timeout == DEFAULT_CONNECT_TIMEOUT
    assert config.read_timeout == DEFAULT_READ_TIMEOUT
    assert config.retries == {"total_max_attempts": DEFAULT_MAX_ATTEMPTS}
    assert util.download_timeout == 30


def test_client_config_from_string_settings():
    settings = dict(
        BASE_SETTINGS,
        max_pool_connections="60",
        connect_timeout="3",
        read_timeout="7.5",
        max_attempts="4",
    )
    util = S3BlobStore(settings)
    config = util._opts["config"]
    assert config.max_pool_connections == 60
    assert config.connect_timeout == 3.0
    assert config.read_timeout == 7.5
    assert config.retries == {"total_max_attempts": 4}
    assert util._s3_request_semaphore._value == 60
    assert util.download_timeout == 4 * (3 + 7.5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        botocore.exceptions.ReadTimeoutError(endpoint_url="http://s3"),
        asyncio.TimeoutError(),
    ],
)
async def test_iter_data_logs_read_failure(caplog, exc):
    mng = S3FileStorageManager(MagicMock(), MagicMock(), MagicMock())
    mng._download = AsyncMock(side_effect=exc)

    with caplog.at_level(logging.ERROR, logger="guillotina_s3storage"):
        with pytest.raises(type(exc)):
            async for _ in mng.iter_data(uri="some/key"):
                pass

    record = next(r for r in caplog.records if r.name == "guillotina_s3storage")
    assert "some/key" in record.getMessage()
    assert record.exc_info[1] is exc


@pytest.mark.asyncio
async def test_pool_wait_is_bounded(aiohttp_server, monkeypatch, caplog):
    requests = []
    release = asyncio.Event()

    async def handler(request):
        requests.append(request.path)
        resp = web.StreamResponse(headers={"ETag": '"v1"'})
        resp.content_length = 2
        await resp.prepare(request)
        await resp.write(b"x")
        await release.wait()
        return resp

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    server = await aiohttp_server(app)

    util = S3BlobStore(
        dict(
            BASE_SETTINGS,
            endpoint_url=str(server.make_url("")),
            ssl=False,
            region_name="us-east-1",
            max_pool_connections=1,
            connect_timeout=0.1,
            read_timeout=0.2,
            max_attempts=1,
        )
    )
    await util.initialize()
    util.get_bucket_name = AsyncMock(return_value="testbucket")
    monkeypatch.setattr(storage, "get_utility", lambda iface: util)

    first = S3FileStorageManager(MagicMock(), MagicMock(), MagicMock())
    first_data = first.iter_data(uri="first")
    assert await first_data.__anext__() == b"x"

    second = S3FileStorageManager(MagicMock(), MagicMock(), MagicMock())
    started = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="guillotina_s3storage"):
        with pytest.raises(asyncio.TimeoutError):
            async for _ in second.iter_data(uri="second"):
                pass

    assert time.monotonic() - started < 2
    assert len(requests) == 1
    assert "second" in caplog.records[-1].getMessage()

    release.set()
    await first_data.aclose()
    await util.finalize()
