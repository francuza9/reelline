"""local: a BlobStore in a folder (`storage.blob.local_dir`). With `storage.blob.local_base_url` set
(a web server or tunnel in front of the folder) `url()` is that base + the key; without, a file://
URI, which is enough for the `local` publisher and for tests but not for a platform to fetch."""

from __future__ import annotations

import shutil
from pathlib import Path

from reelline.config import conf
from reelline.storage.blob import StorageError, no_record


class LocalStore:
    name = "local"

    def __init__(self, root: Path | None = None, base_url: str | None = None, record=no_record):
        cfg = conf().storage.blob
        self.root = Path(root or cfg.local_dir)
        self.base_url = (base_url if base_url is not None else cfg.local_base_url or "").rstrip("/")
        self.record = record

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise StorageError(f"local: key {key!r} leaves the folder")
        return path

    def put(self, key: str, src: Path | bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, bytes):
            path.write_bytes(src)
        else:
            shutil.copyfile(src, path)
        self.record(action="blob_put", method="PUT", endpoint=f"local://{key}", status=200)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as exc:
            raise StorageError(f"local get {key}: {exc}") from None

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int | None:
        path = self._path(key)
        return path.stat().st_size if path.is_file() else None

    def url(self, key: str, expires_s: int | None = None) -> str:
        return f"{self.base_url}/{key}" if self.base_url else self._path(key).as_uri()

    def delete(self, keys: list[str]) -> None:
        for key in keys:
            self._path(key).unlink(missing_ok=True)
            self.record(action="blob_delete", method="DELETE", endpoint=f"local://{key}", status=200)

    def list(self, prefix: str) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and p.relative_to(self.root).as_posix().startswith(prefix)
        )
