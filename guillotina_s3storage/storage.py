# -*- coding: utf-8 -*-
import asyncio
import contextlib
import logging
from typing import Any
from typing import AsyncIterator
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import aiohttp
import backoff
import botocore
from aiobotocore.session import get_session
from botocore.config import Config
from guillotina import configure
from guillotina import task_vars
from guillotina.component import get_utility
from guillotina.db.exceptions import DeleteStorageException
from guillotina.exceptions import FileNotFoundException
from guillotina.files import BaseCloudFile
from guillotina.files.field import BlobMetadata  # type: ignore
from guillotina.files.utils import generate_key
from guillotina.interfaces import IExternalFileStorageManager
from guillotina.interfaces import IFileCleanup
from guillotina.interfaces import IRequest
from guillotina.interfaces import IResource
from guillotina.interfaces.files import IBlobVacuum  # type: ignore
from guillotina.response import HTTPNotFound
from guillotina.response import HTTPPreconditionFailed
from zope.interface import implementer

from guillotina.schema import Object
from guillotina_s3storage.interfaces import IS3BlobStore
from guillotina_s3storage.interfaces import IS3File
from guillotina_s3storage.interfaces import IS3FileField

log = logging.getLogger("guillotina_s3storage")

MAX_SIZE = 1073741824
DEFAULT_MAX_POOL_CONNECTIONS = 30

MIN_UPLOAD_SIZE = 5 * 1024 * 1024
CHUNK_SIZE = MIN_UPLOAD_SIZE
MAX_RETRIES = 5

RETRIABLE_EXCEPTIONS = (
    botocore.exceptions.ClientError,
    aiohttp.client_exceptions.ClientPayloadError,
    botocore.exceptions.BotoCoreError,
)


class IS3FileStorageManager(IExternalFileStorageManager):
    pass


class S3Exception(Exception):
    pass


@implementer(IS3File)
class S3File(BaseCloudFile):
    """File stored in a S3, with a filename."""


def _is_uploaded_file(file):
    return file is not None and isinstance(file, S3File) and file.uri is not None


@implementer(IS3FileField)
class S3FileField(Object):
    """A NamedBlobFile field."""

    _type = S3File
    schema = IS3File

    def __init__(self, **kw):
        if "schema" in kw:
            self.schema = kw.pop("schema")
        super(S3FileField, self).__init__(schema=self.schema, **kw)


@configure.adapter(
    for_=(IResource, IRequest, IS3FileField), provides=IS3FileStorageManager
)
class S3FileStorageManager:
    file_class = S3File

    def __init__(self, context, request, field):
        self.context = context
        self.request = request
        self.field = field

    def should_clean(self, file):
        cleanup = IFileCleanup(self.context, None)
        return cleanup is None or cleanup.should_clean(file=file, field=self.field)

    @backoff.on_exception(backoff.expo, RETRIABLE_EXCEPTIONS, max_tries=3)
    async def _download(self, uri, bucket=None, **kwargs):
        util = get_utility(IS3BlobStore)
        if bucket is None:
            bucket = await util.get_bucket_name()
        async with util.s3_client() as client:
            return await client.get_object(Bucket=bucket, Key=uri, **kwargs)

    async def iter_data(self, uri=None, **kwargs):
        if uri is None:
            file = self.field.query(self.field.context or self.context, None)
            if not _is_uploaded_file(file):
                raise FileNotFoundException("File not found")
            else:
                uri = file.uri

        downloader = await self._download(uri, **kwargs)

        # we do not want to timeout ever from this...
        # downloader['Body'].set_socket_timeout(999999)
        async with downloader["Body"] as stream:
            async for data in stream.content.iter_chunked(CHUNK_SIZE):
                yield data

    async def range_supported(self) -> bool:
        return True

    async def read_range(self, start: int, end: int) -> AsyncIterator[bytes]:
        """
        Iterate through ranges of data
        """
        async for chunk in self.iter_data(Range=f"bytes={start}-{end - 1}"):
            yield chunk

    async def delete_upload(self, uri, bucket=None):
        util = get_utility(IS3BlobStore)
        if bucket is None:
            bucket = await util.get_bucket_name()
        if uri is not None:
            try:
                async with util.s3_client() as client:
                    await client.delete_object(Bucket=bucket, Key=uri)
            except botocore.exceptions.ClientError:
                log.warn("Error deleting object", exc_info=True)
        else:
            raise AttributeError("No valid uri")

    async def _abort_multipart(self, dm):
        util = get_utility(IS3BlobStore)
        try:
            mpu = dm.get("_mpu")
            upload_file_id = dm.get("_upload_file_id")
            bucket_name = dm.get("_bucket_name")
            async with util.s3_client() as client:
                await client.abort_multipart_upload(
                    Bucket=bucket_name, Key=upload_file_id, UploadId=mpu["UploadId"]
                )
        except Exception:
            log.warn("Could not abort multipart upload", exc_info=True)

    async def start(self, dm):
        util = get_utility(IS3BlobStore)
        upload_file_id = dm.get("_upload_file_id")
        if upload_file_id is not None:
            if dm.get("_mpu") is not None:
                await self._abort_multipart(dm)

        bucket_name = await util.get_bucket_name()
        upload_id = generate_key(self.context)
        await dm.update(
            _bucket_name=bucket_name,
            _upload_file_id=upload_id,
            _multipart={"Parts": []},
            _block=1,
            _mpu=await self._create_multipart(bucket_name, upload_id),
        )

    @backoff.on_exception(backoff.expo, RETRIABLE_EXCEPTIONS, max_tries=3)
    async def _create_multipart(self, bucket_name, upload_id):
        util = get_utility(IS3BlobStore)
        async with util.s3_client() as client:
            return await client.create_multipart_upload(
                Bucket=bucket_name, Key=upload_id
            )

    async def append(self, dm, iterable, offset) -> int:
        size = 0
        async for chunk in iterable:
            size += len(chunk)
            part = await self._upload_part(dm, chunk)
            multipart = dm.get("_multipart")
            multipart["Parts"].append(
                {"PartNumber": dm.get("_block"), "ETag": part["ETag"]}
            )
            await dm.update(_multipart=multipart, _block=dm.get("_block") + 1)
        return size

    @backoff.on_exception(backoff.expo, RETRIABLE_EXCEPTIONS, max_tries=3)
    async def _upload_part(self, dm, data):
        util = get_utility(IS3BlobStore)
        async with util.s3_client() as client:
            return await client.upload_part(
                Bucket=dm.get("_bucket_name"),
                Key=dm.get("_upload_file_id"),
                PartNumber=dm.get("_block"),
                UploadId=dm.get("_mpu")["UploadId"],
                Body=data,
            )

    async def finish(self, dm):
        file = self.field.query(self.field.context or self.context, None)
        if _is_uploaded_file(file):
            # delete existing file
            if self.should_clean(file):
                try:
                    await self.delete_upload(file.uri)
                except botocore.exceptions.ClientError:
                    log.error(
                        f"Referenced key {file.uri} could not be found", exc_info=True
                    )
                    log.warn("Error deleting object", exc_info=True)

        if dm.get("_mpu") is not None:
            await self._complete_multipart_upload(dm)
        await dm.update(
            uri=dm.get("_upload_file_id"),
            _multipart=None,
            _mpu=None,
            _block=None,
            _upload_file_id=None,
        )

    @backoff.on_exception(backoff.expo, RETRIABLE_EXCEPTIONS, max_tries=3)
    async def _complete_multipart_upload(self, dm):
        util = get_utility(IS3BlobStore)
        # if blocks is 0, it means the file is of zero length so we need to
        # trick it to finish a multiple part with no data.
        if dm.get("_block") == 1:
            part = await self._upload_part(dm, b"")
            multipart = dm.get("_multipart")
            multipart["Parts"].append(
                {"PartNumber": dm.get("_block"), "ETag": part["ETag"]}
            )
            await dm.update(_multipart=multipart, _block=dm.get("_block") + 1)
        async with util.s3_client() as client:
            await client.complete_multipart_upload(
                Bucket=dm.get("_bucket_name"),
                Key=dm.get("_upload_file_id"),
                UploadId=dm.get("_mpu")["UploadId"],
                MultipartUpload=dm.get("_multipart"),
            )

    async def exists(self):
        bucket = None
        file = self.field.query(self.field.context or self.context, None)
        util = get_utility(IS3BlobStore)
        if not _is_uploaded_file(file):
            return False
        else:
            uri = file.uri
            bucket = await util.get_bucket_name()
        try:
            async with util.s3_client() as client:
                return await client.head_object(Bucket=bucket, Key=uri) is not None
        except botocore.exceptions.ClientError as ex:
            error_code = ex.response["Error"]["Code"]
            # NoSuchKey for potential backwards compatability
            if error_code == "404" or error_code == "NoSuchKey":
                return False
            raise

    async def copy(self, to_storage_manager, to_dm):
        file = self.field.query(self.field.context or self.context, None)
        if not _is_uploaded_file(file):
            raise HTTPNotFound(
                content={"reason": "To copy a uri must be set on the object"}
            )

        util = get_utility(IS3BlobStore)

        new_uri = generate_key(self.context)
        bucket = await util.get_bucket_name()
        async with util.s3_client() as client:
            await client.copy_object(
                CopySource={"Bucket": bucket, "Key": file.uri},
                Bucket=bucket,
                Key=new_uri,
            )
        await to_dm.finish(
            values={
                "content_type": file.content_type,
                "size": file.size,
                "uri": new_uri,
                "filename": file.filename or "unknown",
            }
        )

    async def delete(self):
        file = self.field.get(self.field.context or self.context)
        await self.delete_upload(file.uri)


@implementer(IBlobVacuum)
class S3BlobStore:
    def __init__(self, settings, loop=None):
        self._aws_access_key = settings["aws_client_id"]
        self._aws_secret_key = settings["aws_client_secret"]

        max_pool_connections = settings.get(
            "max_pool_connections", DEFAULT_MAX_POOL_CONNECTIONS
        )
        self._opts = dict(
            aws_secret_access_key=self._aws_secret_key,
            aws_access_key_id=self._aws_access_key,
            endpoint_url=settings.get("endpoint_url"),
            use_ssl=settings.get("ssl", True),
            region_name=settings.get("region_name"),
            config=Config(max_pool_connections=max_pool_connections),
        )

        self.exit_stack = contextlib.AsyncExitStack()
        self._s3aiosession = get_session()
        self._s3_request_semaphore = asyncio.BoundedSemaphore(max_pool_connections)

        self._cached_buckets = []

        self._bucket_name = settings["bucket"]

        self._bucket_name_format = settings.get(
            "bucket_name_format", "{container}{delimiter}{base}"
        )
        self._delimiter = settings.get("bucket_delimiter", None)

    def _get_region_name(self) -> str:
        return self._opts["region_name"]

    @contextlib.asynccontextmanager
    async def s3_client(self):
        async with self._s3_request_semaphore:
            yield self._s3aioclient

    async def _get_or_create_bucket(self, container, bucket_name):
        missing = False
        try:
            async with self.s3_client() as client:
                res = await client.head_bucket(Bucket=bucket_name)
                if res["ResponseMetadata"]["HTTPStatusCode"] == 404:
                    missing = True
        except botocore.exceptions.ClientError as e:
            error_code = int(e.response["Error"]["Code"])
            if error_code == 404:
                missing = True

        if missing:
            async with self.s3_client() as client:
                await client.create_bucket(**self._get_bucket_kargs(bucket_name))

    async def get_bucket_name(self):
        container = task_vars.container.get()
        s3_bucket_override = getattr(container, "bucket_override", None)

        if s3_bucket_override:

            if not await self.check_bucket_accessibility(s3_bucket_override):
                log.error(
                    f"S3 bucket override '{s3_bucket_override}' for container '{container.id}' is not accessible."
                )

                raise HTTPPreconditionFailed(
                    content={"reason": f"Bucket {s3_bucket_override} is not accessible"}
                )
            else:
                return s3_bucket_override

        if self._delimiter:
            char_delimiter = self._delimiter
        else:
            char_delimiter = "." if "." in self._bucket_name else "-"

        bucket_name = self._bucket_name_format.format(
            container=container.id.lower(),
            delimiter=char_delimiter,
            base=self._bucket_name,
        )

        bucket_name = bucket_name.replace("_", "-")

        if bucket_name in self._cached_buckets:
            return bucket_name

        await self._get_or_create_bucket(container, bucket_name)

        self._cached_buckets.append(bucket_name)
        return bucket_name

    async def initialize(self, app=None):
        # No asyncio loop to run
        self.app = app
        self._s3aioclient = await self.exit_stack.enter_async_context(
            self._s3aiosession.create_client("s3", **self._opts)
        )

    async def finalize(self, app=None):
        await self._s3aioclient.close()
        await self.exit_stack.aclose()

    async def iterate_bucket(self):
        container = task_vars.container.get()
        bucket_name = await self.get_bucket_name()
        async with self.s3_client() as client:
            result = await client.list_objects(
                Bucket=bucket_name, Prefix=container.id + "/"
            )
        async with self.s3_client() as client:
            paginator = client.get_paginator("list_objects")
            async for result in paginator.paginate(
                Bucket=bucket_name, Prefix=container.id + "/"
            ):
                for item in result.get("Contents", []):
                    yield item

    def _get_bucket_kargs(self, bucket_name: str):
        bucket_kwargs: Dict[str, Any] = {"Bucket": bucket_name}
        if self._get_region_name() != "us-east-1":
            bucket_kwargs["CreateBucketConfiguration"] = {
                "LocationConstraint": self._get_region_name(),
            }
        return bucket_kwargs

    async def iterate_bucket_page(self, page_token=None, prefix=None, max_keys=1000):
        container = task_vars.container.get()
        bucket_name = await self.get_bucket_name()
        async with self.s3_client() as client:
            args = {
                "Bucket": bucket_name,
                "Prefix": prefix or container.id + "/",
            }
            if page_token:
                args["ContinuationToken"] = page_token
            if max_keys:
                args["MaxKeys"] = max_keys
            return await client.list_objects_v2(**args)

    async def get_blobs(
        self, page_token: Optional[str] = None, prefix=None, max_keys=1000
    ) -> Tuple[List[BlobMetadata], str]:
        """
        Get a page of items from the bucket
        """
        container = task_vars.container.get()
        bucket_name = await self.get_bucket_name()
        async with self.s3_client() as client:
            args = {
                "Bucket": bucket_name,
                "Prefix": prefix or container.id + "/",  # type: ignore
            }

            if page_token:
                args["ContinuationToken"] = page_token
            if max_keys:
                args["MaxKeys"] = max_keys

            response = await client.list_objects_v2(**args)

            blobs = [
                BlobMetadata(
                    name=item["Key"],
                    bucket=bucket_name,
                    size=int(item["Size"]),
                    createdTime=item["LastModified"],
                )
                for item in response["Contents"]
            ]
            next_page_token = response.get("NextContinuationToken", None)

            return blobs, next_page_token

    async def delete_blobs(
        self, keys: List[str], bucket_name: Optional[str] = None
    ) -> Tuple[List[str], List[str]]:
        """
        Deletes a batch of files.  Returns successful and failed keys.
        """

        if not bucket_name:
            bucket_name = await self.get_bucket_name()

        async with self.s3_client() as client:
            args = {
                "Bucket": bucket_name,
                "Delete": {"Objects": [{"Key": key} for key in keys]},
            }

            response = await client.delete_objects(**args)
            success_blobs = response.get("Deleted", [])
            success_keys = [o["Key"] for o in success_blobs]
            failed_blobs = response.get("Errors", [])
            failed_keys = [o["Key"] for o in failed_blobs]

            return success_keys, failed_keys

    async def delete_bucket(self, bucket_name: Optional[str] = None):
        """
        Delete the given bucket
        """
        async with self.s3_client() as client:
            if not bucket_name:
                bucket_name = await self.get_bucket_name()

            args = {
                "Bucket": bucket_name,
            }

            response = await client.delete_bucket(**args)

            if response["ResponseMetadata"]["HTTPStatusCode"] != 204:
                raise DeleteStorageException()

    async def check_bucket_accessibility(self, bucket_name: str) -> bool:
        """
        Check if the bucket is accessible.
        """
        try:
            async with self.s3_client() as client:
                await client.head_bucket(Bucket=bucket_name)
            return True
        except botocore.exceptions.ClientError as e:
            error_code = int(e.response["Error"]["Code"])
            if error_code == 404:
                return False
            raise S3Exception(f"Error checking bucket accessibility: {e}")
