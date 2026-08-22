from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import BinaryIO, Callable, Iterable

import boto3
import structlog
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import settings
from app.services.storage.base import (
    StorageBackendError,
    StorageObject,
    StorageService,
    UploadItem,
    UploadResult,
)

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
        public_endpoint_url: str | None = None,
    ):
        self._name = name
        # max_pool_connections/retries: benchmarked directly against real
        # MinIO with a large Zarr store's many small chunk files (see
        # Settings.STORAGE_MAX_POOL_CONNECTIONS's docstring) — the default
        # boto3 pool (10) caps concurrent put_many() throughput regardless
        # of how many worker threads are used, since threads beyond the
        # pool size just queue on connection checkout. Standard retry mode
        # covers transient failures (connection reset, timeout, 5xx) on
        # every call this client makes, including inside put_many()'s
        # threads, without a custom retry loop (Case 6: MinIO temporarily
        # unavailable).
        boto_config = BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            max_pool_connections=settings.STORAGE_MAX_POOL_CONNECTIONS,
            retries={"max_attempts": settings.STORAGE_MAX_RETRIES, "mode": "standard"},
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=boto_config,
        )
        # Presigned URLs are followed directly by an external client (a
        # browser, curl) — never routed through the backend — so they must
        # be built against the endpoint that's actually reachable from
        # outside the Docker network, which differs from the internal
        # endpoint_url the backend/worker containers use for their own
        # get/put calls (e.g. "minio:9000" internally vs. a public
        # host/domain for MinIO's published port, or the same value in a
        # real single-endpoint S3/R2/B2 production deployment).
        self._presign_client = (
            boto3.client(
                "s3",
                endpoint_url=public_endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
            if public_endpoint_url and public_endpoint_url != endpoint_url
            else self._client
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

    def put_many(
        self,
        bucket: str,
        items: Iterable[UploadItem],
        *,
        content_type: str | None = None,
        concurrency: int = 1,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[UploadResult]:
        """Bounded-concurrency override of the base sequential
        implementation — a real ThreadPoolExecutor with `concurrency`
        workers, each uploading one object at a time via the same `put()`
        (and therefore the same streaming `upload_fileobj`, never reading
        a whole file into memory). `items` is consumed into a list once
        (each element is a tiny path+key tuple, not file content) so
        progress can be reported against a known total; no file's bytes
        are read until the specific worker handling that item opens it.

        Verified via direct benchmark against real MinIO with ~14,600
        real Zarr chunk files (~55KB median): concurrency=16 against a
        32-connection pool reached ~111 files/s vs ~54 files/s sequential
        — see Settings.STORAGE_UPLOAD_CONCURRENCY's docstring for the full
        sweep. One item's failure never cancels or blocks the others —
        every item still gets exactly one UploadResult, matching the base
        implementation's contract, so a caller sees the complete picture
        of what succeeded and what didn't even under partial failure."""
        items = list(items)
        total = len(items)
        if total == 0:
            return []

        results_by_key: dict[str, UploadResult] = {}
        completed = 0

        def upload_one(item: UploadItem) -> UploadResult:
            try:
                with open(item.local_path, "rb") as f:
                    self.put(bucket, item.key, f, content_type=content_type)
                return UploadResult(key=item.key, ok=True)
            except Exception as exc:
                return UploadResult(key=item.key, ok=False, error=str(exc))

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
            futures = {executor.submit(upload_one, item): item for item in items}
            for future in as_completed(futures):
                result = future.result()
                results_by_key[result.key] = result
                completed += 1
                if on_progress is not None:
                    on_progress(completed, total)

        # Preserve input order in the returned list — callers (e.g. an
        # all-succeeded fast-path check) shouldn't have to care that
        # completion order differs from submission order.
        return [results_by_key[item.key] for item in items]

    def get(self, bucket: str, key: str) -> BinaryIO:
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            logger.exception("storage.get_failed", backend=self._name, bucket=bucket, key=key)
            raise StorageBackendError(f"Failed to read {bucket}/{key}") from exc
        return response["Body"]

    def presign_get(self, bucket: str, key: str, *, expires_in_seconds: int = 3600) -> str:
        try:
            return self._presign_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
            )
        except ClientError as exc:
            raise StorageBackendError(f"Failed to presign GET for {bucket}/{key}") from exc

    def presign_put(self, bucket: str, key: str, *, expires_in_seconds: int = 3600) -> str:
        try:
            return self._presign_client.generate_presigned_url(
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

    def delete_prefix(self, bucket: str, prefix: str) -> None:
        """PLAN.md Phase 5 — deletes every object under `prefix` (a Zarr
        store's many small chunk/metadata objects), paginated so this
        works regardless of how many chunks a large store has, and
        batched via delete_objects (up to 1000 keys per call — S3's own
        limit) rather than one delete_object call per chunk."""
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if keys:
                    self._client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
        except ClientError as exc:
            logger.exception("storage.delete_prefix_failed", backend=self._name, bucket=bucket, prefix=prefix)
            raise StorageBackendError(f"Failed to delete prefix {bucket}/{prefix}") from exc

    def exists(self, bucket: str, key: str) -> bool:
        return self.stat(bucket, key) is not None

    def list_keys(self, bucket: str, prefix: str):
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    yield obj["Key"]
        except ClientError as exc:
            logger.exception("storage.list_keys_failed", backend=self._name, bucket=bucket, prefix=prefix)
            raise StorageBackendError(f"Failed to list keys under {bucket}/{prefix}") from exc

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

    def create_multipart_upload(
        self, bucket: str, key: str, *, content_type: str | None = None
    ) -> str:
        extra_args: dict = {}
        if content_type:
            extra_args["ContentType"] = content_type
        try:
            response = self._client.create_multipart_upload(Bucket=bucket, Key=key, **extra_args)
        except ClientError as exc:
            logger.exception(
                "storage.create_multipart_upload_failed", backend=self._name, bucket=bucket, key=key
            )
            raise StorageBackendError(f"Failed to start multipart upload for {bucket}/{key}") from exc
        return response["UploadId"]

    def presign_upload_part(
        self,
        bucket: str,
        key: str,
        *,
        upload_id: str,
        part_number: int,
        expires_in_seconds: int = 3600,
    ) -> str:
        try:
            return self._presign_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=expires_in_seconds,
            )
        except ClientError as exc:
            raise StorageBackendError(
                f"Failed to presign part {part_number} for {bucket}/{key}"
            ) from exc

    def complete_multipart_upload(
        self, bucket: str, key: str, *, upload_id: str, parts: list[dict]
    ) -> StorageObject:
        try:
            self._client.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except ClientError as exc:
            logger.exception(
                "storage.complete_multipart_upload_failed", backend=self._name, bucket=bucket, key=key
            )
            raise StorageBackendError(f"Failed to complete multipart upload for {bucket}/{key}") from exc

        stat = self.stat(bucket, key)
        if stat is None:
            raise StorageBackendError(
                f"Multipart upload to {bucket}/{key} reported success but object not found"
            )
        return stat

    def abort_multipart_upload(self, bucket: str, key: str, *, upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            # NoSuchUpload means the session is already gone (already
            # completed, already aborted, or expired) — idempotent no-op,
            # matching delete()'s "absent is success" contract.
            if error_code == "NoSuchUpload":
                return
            logger.exception(
                "storage.abort_multipart_upload_failed", backend=self._name, bucket=bucket, key=key
            )
            raise StorageBackendError(f"Failed to abort multipart upload for {bucket}/{key}") from exc

    def list_parts(self, bucket: str, key: str, *, upload_id: str) -> list[dict]:
        try:
            response = self._client.list_parts(Bucket=bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchUpload":
                return []
            logger.exception("storage.list_parts_failed", backend=self._name, bucket=bucket, key=key)
            raise StorageBackendError(f"Failed to list parts for {bucket}/{key}") from exc

        return [
            {
                "PartNumber": p["PartNumber"],
                "ETag": p["ETag"].strip('"'),
                "Size": p["Size"],
            }
            for p in response.get("Parts", [])
        ]

    def set_public_prefix_policy(self, bucket: str, prefix: str) -> None:
        # put_bucket_policy REPLACES the whole policy document — this bucket
        # can have multiple public prefixes (avatars/, media/) granted at
        # different times by different callers, so this must merge with
        # whatever policy already exists rather than clobbering it. Read
        # the current policy first (absent/malformed = start fresh), add
        # this prefix's statement if it isn't already present, write back
        # the union.
        import json

        resource = f"arn:aws:s3:::{bucket}/{prefix}*"
        try:
            existing_raw = self._client.get_bucket_policy(Bucket=bucket)["Policy"]
            policy = json.loads(existing_raw)
        except ClientError:
            policy = {"Version": "2012-10-17", "Statement": []}

        already_present = any(
            resource in (stmt.get("Resource") if isinstance(stmt.get("Resource"), list) else [stmt.get("Resource")])
            for stmt in policy.get("Statement", [])
        )
        if not already_present:
            policy.setdefault("Statement", []).append(
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [resource],
                }
            )

        try:
            self._client.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
        except ClientError as exc:
            logger.exception(
                "storage.set_public_prefix_policy_failed", backend=self._name, bucket=bucket, prefix=prefix
            )
            raise StorageBackendError(f"Failed to set public policy on {bucket}/{prefix}") from exc
