"""reelline/storage: the BlobStore adapters (local folder, S3 on a fake client, memory), the registry."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from reelline.config import conf
from reelline.publish.base import PublisherError  # noqa: F401  (the error families stay apart)
from reelline.storage.blob import BlobStore, StorageError, key_in, new_key, strip_query
from reelline.storage.fakes import MemoryStore
from reelline.storage.local import LocalStore
from reelline.storage.registry import get_blob_store
from reelline.storage.s3 import S3Store


def contract(store: BlobStore, tmp_path):
    f = tmp_path / "reel.mp4"
    f.write_bytes(b"12345")
    assert isinstance(store, BlobStore) and not store.exists("reels/1-x/reel.mp4")
    store.put("reels/1-x/reel.mp4", f, "video/mp4")
    store.put("reels/1-x/note.txt", b"hi")
    assert store.exists("reels/1-x/reel.mp4") and store.size("reels/1-x/reel.mp4") == 5
    assert store.get("reels/1-x/note.txt") == b"hi" and store.get("reels/1-x/reel.mp4") == b"12345"
    assert store.list("reels/1-x/") == ["reels/1-x/note.txt", "reels/1-x/reel.mp4"] and store.list("reels/2") == []
    assert "reels/1-x/reel.mp4" in store.url("reels/1-x/reel.mp4")
    store.delete(["reels/1-x/reel.mp4", "reels/1-x/note.txt"])
    assert store.list("reels/") == [] and store.size("reels/1-x/reel.mp4") is None
    with pytest.raises(StorageError):
        store.get("reels/1-x/reel.mp4")


def test_memory_and_local_stores_obey_the_contract(tmp_path):
    contract(MemoryStore(), tmp_path)
    contract(LocalStore(tmp_path / "blobs", base_url="https://files.test"), tmp_path)


def test_local_urls_and_the_folder_boundary(tmp_path):
    store = LocalStore(tmp_path / "blobs", base_url="https://files.test/")
    assert store.url("a/b.mp4") == store.url("a/b.mp4", 60) == "https://files.test/a/b.mp4"
    assert LocalStore(tmp_path / "blobs", base_url="").url("a.mp4").startswith("file://")
    with pytest.raises(StorageError, match="leaves the folder"):
        store.put("../escape", b"x")


def test_memory_store_signed_urls_record_their_lifetime():
    m = MemoryStore()
    assert m.url("k") == "https://blobs.test/k" and m.url("k", 90).endswith("exp=90") and m.signed == [("k", 90)]


class S3Fake:
    """The few boto3 calls the adapter makes, on a dict."""

    def __init__(self):
        self.objects, self.uploads, self.fail = {}, [], None

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError("404")
        return {"ContentLength": len(self.objects[Key])}

    def upload_file(self, path, bucket, key, ExtraArgs):
        if self.fail:
            raise ClientError(self.fail)
        self.uploads.append((key, ExtraArgs))
        self.objects[key] = Path(path).read_bytes()

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError("404")
        return {"Body": SimpleNamespace(read=lambda: self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)]}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.expires = ExpiresIn
        return f"https://s3.test/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=SECRET"


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": int(code) if code.isdigit() else 500},
        }


def s3(tmp_path, public="https://pub.test"):
    calls = []
    return S3Store(
        SimpleNamespace(bucket="b", public_url=public), client=S3Fake(), record=lambda **kw: calls.append(kw)
    ), calls


def test_s3_store_obeys_the_contract(tmp_path):
    store, _ = s3(tmp_path)
    contract(store, tmp_path)


def test_s3_skips_an_upload_that_is_there_and_logs_no_signature(tmp_path):
    store, calls = s3(tmp_path)
    f = tmp_path / "r.mp4"
    f.write_bytes(b"abc")
    store.put("k/r.mp4", f, "video/mp4")
    store.put("k/r.mp4", f, "video/mp4")  # a retry: same size, nothing to do
    assert [u[0] for u in store.client.uploads] == ["k/r.mp4"] and store.client.uploads[0][1] == {
        "ContentType": "video/mp4"
    }
    signed = store.url("k/r.mp4", 30 * 24 * 3600)
    assert store.client.expires == 7 * 24 * 3600 and "SECRET" in signed
    assert store.url("k/r.mp4") == "https://pub.test/k/r.mp4"
    assert "SECRET" not in repr(calls) and any(c["action"] == "r2_presign" for c in calls)


def test_s3_errors_become_storage_errors_with_the_status(tmp_path):
    store, calls = s3(tmp_path)
    store.client.fail = "503"
    f = tmp_path / "r.mp4"
    f.write_bytes(b"abc")
    with pytest.raises(StorageError, match="R2 r2_upload k/r.mp4: ClientError"):
        store.put("k/r.mp4", f)
    assert calls[-1]["status"] == 503 and "error" in calls[-1]
    with pytest.raises(StorageError, match="R2_PUBLIC_URL"):
        s3(tmp_path, public=None)[0].url("k")


def test_registry(tmp_path, monkeypatch):
    mem = MemoryStore()
    assert get_blob_store(mem) is mem
    monkeypatch.setattr(conf().storage.blob, "kind", "s3")
    assert get_blob_store(S3Fake(), settings=SimpleNamespace(bucket="b")).name == "s3"
    monkeypatch.setattr(conf().storage.blob, "kind", "local")
    monkeypatch.setattr(conf().storage.blob, "local_dir", tmp_path)
    assert get_blob_store().name == "local"
    monkeypatch.setattr(conf().storage.blob, "kind", "gcs")
    with pytest.raises(ValueError, match="no blob store 'gcs'"):
        get_blob_store()


def test_key_helpers():
    key = new_key(7)
    assert (
        key.startswith("reels/7-") and key.endswith("/reel.mp4") and key_in(key, "reel_x.mp4").endswith("/reel_x.mp4")
    )
    assert strip_query("https://h/p?X-Amz-Signature=S") == "https://h/p"
