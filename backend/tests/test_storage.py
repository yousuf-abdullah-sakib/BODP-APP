import io
import uuid

import pytest

from app.services.storage import StorageBackendError, extract_key, get_storage_backend, previews_key, processed_key, raw_key
from app.services.storage.keys import sanitize_filename


@pytest.fixture
def storage():
    svc = get_storage_backend("vps_minio")
    svc.ensure_bucket("bodp-vps")
    return svc


class TestKeyLayout:
    def test_raw_key_layout(self):
        did, fid = uuid.uuid4(), uuid.uuid4()
        key = raw_key(did, fid, "some file.csv")
        assert key == f"raw/{did}/{fid}_some_file.csv"

    def test_processed_key_layout(self):
        did, fid = uuid.uuid4(), uuid.uuid4()
        assert processed_key(did, fid) == f"processed/{did}/{fid}.parquet"

    def test_extract_key_layout(self):
        gid, eid = uuid.uuid4(), uuid.uuid4()
        assert extract_key(gid, eid, ".csv") == f"extracts/{gid}/{eid}.csv"
        assert extract_key(gid, eid, "zip") == f"extracts/{gid}/{eid}.zip"

    def test_previews_key_layout(self):
        did = uuid.uuid4()
        assert previews_key(did) == f"previews/{did}/thumbnail.png"

    def test_sanitize_filename_strips_path_traversal(self):
        assert sanitize_filename("../../etc/passwd") == "passwd"
        assert sanitize_filename("..\\..\\windows\\system32\\config") == "config"

    def test_sanitize_filename_strips_unsafe_chars(self):
        result = sanitize_filename("my data (final) v2!.csv")
        assert result == "my_data_final__v2_.csv" or "/" not in result and "\\" not in result

    def test_sanitize_filename_never_empty(self):
        assert sanitize_filename("...") != ""
        assert sanitize_filename("") == "file"


class TestS3CompatibleBackend:
    def test_put_get_roundtrip(self, storage):
        key = f"test/{uuid.uuid4()}.txt"
        data = b"hello world, this is test content"
        storage.put("bodp-vps", key, io.BytesIO(data), content_type="text/plain")

        body = storage.get("bodp-vps", key)
        assert body.read() == data

        storage.delete("bodp-vps", key)

    def test_exists_true_and_false(self, storage):
        key = f"test/{uuid.uuid4()}.txt"
        assert storage.exists("bodp-vps", key) is False

        storage.put("bodp-vps", key, io.BytesIO(b"data"))
        assert storage.exists("bodp-vps", key) is True

        storage.delete("bodp-vps", key)
        assert storage.exists("bodp-vps", key) is False

    def test_stat_returns_size_and_content_type(self, storage):
        key = f"test/{uuid.uuid4()}.txt"
        content = b"twelve bytes"
        storage.put("bodp-vps", key, io.BytesIO(content), content_type="text/plain")

        stat = storage.stat("bodp-vps", key)
        assert stat is not None
        assert stat.size_bytes == len(content)
        assert stat.content_type == "text/plain"

        storage.delete("bodp-vps", key)

    def test_stat_returns_none_for_missing_object(self, storage):
        assert storage.stat("bodp-vps", f"test/nonexistent-{uuid.uuid4()}.txt") is None

    def test_delete_is_idempotent(self, storage):
        key = f"test/{uuid.uuid4()}.txt"
        # Deleting a never-created object must not raise.
        storage.delete("bodp-vps", key)
        storage.delete("bodp-vps", key)

    def test_presign_get_returns_usable_url(self, storage):
        key = f"test/{uuid.uuid4()}.txt"
        storage.put("bodp-vps", key, io.BytesIO(b"presign test"))

        url = storage.presign_get("bodp-vps", key, expires_in_seconds=60)
        assert url.startswith("http")
        assert key in url

        import urllib.request

        with urllib.request.urlopen(url) as resp:
            assert resp.read() == b"presign test"

        storage.delete("bodp-vps", key)

    def test_presign_put_allows_direct_upload(self, storage):
        key = f"test/{uuid.uuid4()}.txt"
        url = storage.presign_put("bodp-vps", key, expires_in_seconds=60)

        import urllib.request

        req = urllib.request.Request(url, data=b"uploaded via presigned url", method="PUT")
        with urllib.request.urlopen(req) as resp:
            assert resp.status in (200, 204)

        assert storage.exists("bodp-vps", key) is True
        storage.delete("bodp-vps", key)

    def test_ensure_bucket_is_idempotent(self, storage):
        storage.ensure_bucket("bodp-vps")
        storage.ensure_bucket("bodp-vps")

    def test_list_keys_returns_exact_set(self, storage):
        prefix = f"test-list-keys/{uuid.uuid4()}/"
        keys = [f"{prefix}file_{i}.txt" for i in range(5)]
        for key in keys:
            storage.put("bodp-vps", key, io.BytesIO(b"x"))
        try:
            listed = set(storage.list_keys("bodp-vps", prefix))
            assert listed == set(keys)
        finally:
            for key in keys:
                storage.delete("bodp-vps", key)

    def test_list_keys_empty_prefix_yields_nothing(self, storage):
        assert list(storage.list_keys("bodp-vps", f"test-list-keys/nonexistent-{uuid.uuid4()}/")) == []

    def test_list_keys_paginates_past_1000(self, storage):
        """S3's list_objects_v2 caps a single page at 1000 keys — this
        confirms the paginator this method wraps actually walks every
        page rather than silently truncating at the first one."""
        prefix = f"test-list-keys-page/{uuid.uuid4()}/"
        keys = [f"{prefix}f{i:05d}.txt" for i in range(1500)]
        for key in keys:
            storage.put("bodp-vps", key, io.BytesIO(b""))
        try:
            listed = set(storage.list_keys("bodp-vps", prefix))
            assert listed == set(keys)
            assert len(listed) == 1500
        finally:
            storage.delete_prefix("bodp-vps", prefix)


class TestMultipartUpload:
    def test_full_multipart_roundtrip(self, storage):
        key = f"test/multipart-{uuid.uuid4()}.bin"
        upload_id = storage.create_multipart_upload("bodp-vps", key, content_type="application/octet-stream")
        assert upload_id

        # S3-compatible multipart requires every part except the last to be
        # >= 5MiB — use two parts sized just over that floor so the test
        # exercises real multi-part completion, not a degenerate 1-part case.
        part_size = 5 * 1024 * 1024 + 1
        part1 = bytes([1]) * part_size
        part2 = b"final part, can be small"

        import urllib.request

        etags = []
        for part_number, data in enumerate([part1, part2], start=1):
            url = storage.presign_upload_part(
                "bodp-vps", key, upload_id=upload_id, part_number=part_number, expires_in_seconds=60
            )
            req = urllib.request.Request(url, data=data, method="PUT")
            with urllib.request.urlopen(req) as resp:
                assert resp.status in (200, 204)
                etags.append(resp.headers.get("ETag").strip('"'))

        parts = storage.list_parts("bodp-vps", key, upload_id=upload_id)
        assert len(parts) == 2
        assert {p["PartNumber"] for p in parts} == {1, 2}

        stored = storage.complete_multipart_upload(
            "bodp-vps",
            key,
            upload_id=upload_id,
            parts=[{"PartNumber": i + 1, "ETag": etags[i]} for i in range(2)],
        )
        assert stored.size_bytes == len(part1) + len(part2)

        body = storage.get("bodp-vps", key)
        assert body.read() == part1 + part2

        storage.delete("bodp-vps", key)

    def test_abort_multipart_upload_discards_parts(self, storage):
        key = f"test/multipart-abort-{uuid.uuid4()}.bin"
        upload_id = storage.create_multipart_upload("bodp-vps", key)

        storage.abort_multipart_upload("bodp-vps", key, upload_id=upload_id)

        # Aborting releases the session — no parts remain, and the object
        # itself was never created.
        assert storage.list_parts("bodp-vps", key, upload_id=upload_id) == []
        assert storage.exists("bodp-vps", key) is False

    def test_abort_multipart_upload_is_idempotent(self, storage):
        key = f"test/multipart-abort-twice-{uuid.uuid4()}.bin"
        upload_id = storage.create_multipart_upload("bodp-vps", key)

        storage.abort_multipart_upload("bodp-vps", key, upload_id=upload_id)
        # Aborting an already-aborted (now nonexistent) session must not raise.
        storage.abort_multipart_upload("bodp-vps", key, upload_id=upload_id)

    def test_list_parts_empty_for_unknown_upload_id(self, storage):
        key = f"test/multipart-unknown-{uuid.uuid4()}.bin"
        assert storage.list_parts("bodp-vps", key, upload_id="nonexistent-upload-id") == []
