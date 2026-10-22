import asyncio
import logging
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import botocore.exceptions
import pytest

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
    config = S3BlobStore(dict(BASE_SETTINGS))._opts["config"]
    assert config.max_pool_connections == DEFAULT_MAX_POOL_CONNECTIONS
    assert config.connect_timeout == DEFAULT_CONNECT_TIMEOUT
    assert config.read_timeout == DEFAULT_READ_TIMEOUT
    assert config.retries == {"total_max_attempts": DEFAULT_MAX_ATTEMPTS}


def test_client_config_from_string_settings():
    # G_ environment overrides deliver values as strings
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
