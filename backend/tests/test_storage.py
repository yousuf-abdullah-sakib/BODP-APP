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
