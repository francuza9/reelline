"""storage.blob.kind -> the BlobStore: get_blob_store(). `given` mirrors reelline/notify/registry.py: a
BlobStore is used as it is; anything else (a boto3 client, a test's fake of one) becomes the
connection of the s3 adapter; None: the configured adapter, built from the env file."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from reelline.config import conf
from reelline.storage.blob import BlobStore, no_record


def get_blob_store(
    given: Any = None, kind: str | None = None, *, record: Callable[..., None] = no_record, settings: Any = None
) -> BlobStore:
    if isinstance(given, BlobStore):
        return given
    kind = kind or conf().storage.blob.kind
    if kind == "s3":
        from reelline.storage.s3 import S3Store

        return S3Store(settings, client=given, record=record)
    if kind == "local":
        from reelline.storage.local import LocalStore

        return LocalStore(record=record)
    raise ValueError(f"no blob store {kind!r} (storage.blob.kind: s3, local)")
