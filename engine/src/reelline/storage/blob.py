"""BlobStore: where a reel's files wait while the platforms fetch them.

`put` stores a file or bytes under a key, `get` reads it back, `url` says where it can be fetched
(`expires_s` None: the public URL; a number of seconds: a URL that stops working then), `exists` /
`size` / `list` / `delete` do what they say. Keys are plain strings with `/`. Adapters: `s3`
(reelline/storage/s3.py: AWS S3 and anything that speaks it, Cloudflare R2 included) and `local`
(reelline/storage/local.py: a folder).

Every call goes to `record` (a keyword-only callback, `no_record` by default): the action, the
object's address and the result, never a secret or a URL's signature.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from reelline.config import conf
from reelline.core.errors import ReellineError

MAX_SIGNED_S = 7 * 24 * 3600  # SigV4's limit for a presigned URL


class StorageError(ReellineError, RuntimeError):
    """A storage call that failed (the message says which, never a secret)."""


@runtime_checkable
class BlobStore(Protocol):
    name: str

    def put(self, key: str, src: Path | bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int | None: ...
    def url(self, key: str, expires_s: int | None = None) -> str: ...
    def delete(self, keys: list[str]) -> None: ...
    def list(self, prefix: str) -> list[str]: ...


def no_record(**kw: Any) -> None:
    pass


def new_key(reel_id: int, file: str = "reel.mp4") -> str:
    """reels/<reel id>-<32 random hex>/<file>: unguessable, since it's public."""
    import secrets

    return conf().publish.r2_key.format(reel_id=reel_id, token=secrets.token_hex(16), file=file)


def key_in(prefix_of: str, file: str) -> str:
    """The key of `file` under the same unguessable prefix as the key `prefix_of`."""
    return f"{prefix_of.rsplit('/', 1)[0]}/{file}"


def strip_query(url: str) -> str:
    """A URL without its query string (a presigned URL's signature lives there)."""
    return url.split("?", 1)[0]
