from typing import BinaryIO

import boto3
import structlog
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.services.storage.base import StorageBackendError, StorageObject, StorageService

logger = structlog.get_logger(__name__)


class S3CompatibleBackend(StorageService):
    """boto3-based implementation of StorageService, working against any
    S3-compatible endpoint — MinIO on the VPS today, and whichever cloud
    provider (AWS S3 / B2 / R2 / Wasabi) gets selected later, unchanged
    (Master Plan §0: "storage service is built against the S3 API so the
    backend never changes when the provider does").

    One class serves both the "MinIOBackend" and "S3CompatibleBackend" roles
    named in Master Plan §3 Phase 2 task 1 — they are the same boto3 S3
    client API, differing only in endpoint/credentials, which the registry
    module supplies via two separate factory calls.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        name: str = "s3",
    ):
        self._name = name
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put(
        self,
        bucket: str,
        key: str,
        data: BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> StorageObject:
        extra_args: dict = {}
        if content_type:
            extra_args["ContentType"] = content_type
        if metadata:
            extra_args["Metadata"] = metadata

        try:
            # upload_fileobj streams in chunks (managed multipart transfer for
            # large files) rather than reading the whole object into memory.
            self._client.upload_fileobj(data, bucket, key, ExtraArgs=extra_args or None)
        except ClientError as exc:
            logger.exception("storage.put_failed", backend=self._name, bucket=bucket, key=key)
            raise StorageBackendError(f"Failed to store {bucket}/{key}") from exc

        stat = self.stat(bucket, key)
        if stat is None:
            raise StorageBackendError(f"Upload to {bucket}/{key} reported success but object not found")
        return stat

    def get(self, bucket: str, key: str) -> BinaryIO:
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            logger.exception("storage.get_failed", backend=self._name, bucket=bucket, key=key)
            raise StorageBackendError(f"Failed to read {bucket}/{key}") from exc
        return response["Body"]

    def presign_get(self, bucket: str, key: str, *, expires_in_seconds: int = 3600) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
            )
        except ClientError as exc:
            raise StorageBackendError(f"Failed to presign GET for {bucket}/{key}") from exc

    def presign_put(self, bucket: str, key: str, *, expires_in_seconds: int = 3600) -> str:
        try:
            return self._client.generate_presigned_url(
                "put_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
            )
        except ClientError as exc:
            raise StorageBackendError(f"Failed to presign PUT for {bucket}/{key}") from exc

    def delete(self, bucket: str, key: str) -> None:
        try:
            self._client.delete_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            # delete_object on S3-compatible stores is normally idempotent
            # (204 even if absent), so a ClientError here is a real failure —
            # surface it rather than swallowing silently.
            logger.exception("storage.delete_failed", backend=self._name, bucket=bucket, key=key)
            raise StorageBackendError(f"Failed to delete {bucket}/{key}") from exc

    def exists(self, bucket: str, key: str) -> bool:
        return self.stat(bucket, key) is not None

    def stat(self, bucket: str, key: str) -> StorageObject | None:
        try:
            response = self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return None
            logger.exception("storage.stat_failed", backend=self._name, bucket=bucket, key=key)
            raise StorageBackendError(f"Failed to stat {bucket}/{key}") from exc

        return StorageObject(
            bucket=bucket,
            key=key,
            size_bytes=response["ContentLength"],
            etag=response.get("ETag", "").strip('"') or None,
            content_type=response.get("ContentType"),
        )

    def ensure_bucket(self, bucket: str) -> None:
        try:
            self._client.head_bucket(Bucket=bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=bucket)
            except ClientError as exc:
                logger.exception("storage.ensure_bucket_failed", backend=self._name, bucket=bucket)
                raise StorageBackendError(f"Failed to create bucket {bucket}") from exc
