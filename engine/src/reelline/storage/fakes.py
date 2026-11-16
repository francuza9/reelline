"""MemoryStore: a BlobStore in a dict (no disk, no network)."""

from __future__ import annotations

from pathlib import Path

from reelline.storage.blob import StorageError


class MemoryStore:
    name = "memory"

    def __init__(self, base_url: str = "https://blobs.test", fail: bool = False):
        self.base_url, self.fail = base_url.rstrip("/"), fail
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.signed: list[tuple[str, int]] = []

    def _check(self) -> None:
        if self.fail:
            raise StorageError("memory: down")

    def put(self, key: str, src: Path | bytes, content_type: str = "application/octet-stream") -> None:
        self._check()
        self.objects[key] = src if isinstance(src, bytes) else Path(src).read_bytes()

    def get(self, key: str) -> bytes:
        self._check()
        if key not in self.objects:
            raise StorageError(f"memory get {key}: not found")
        return self.objects[key]

    def exists(self, key: str) -> bool:
        return key in self.objects

    def size(self, key: str) -> int | None:
        return len(self.objects[key]) if key in self.objects else None

    def url(self, key: str, expires_s: int | None = None) -> str:
        if expires_s is None:
            return f"{self.base_url}/{key}"
        self.signed.append((key, expires_s))
        return f"{self.base_url}/{key}?sig=x&exp={expires_s}"

    def delete(self, keys: list[str]) -> None:
        self._check()
        for k in keys:
            self.objects.pop(k, None)
            self.deleted.append(k)

    def list(self, prefix: str) -> list[str]:
        self._check()
        return sorted(k for k in self.objects if k.startswith(prefix))
