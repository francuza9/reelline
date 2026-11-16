"""s3: the BlobStore over the S3 API (boto3, SigV4): AWS S3 or anything compatible. Cloudflare R2 is
one (region "auto"): The host's reel files under one unguessable prefix (publish.r2_key) in R2_BUCKET,
presigned GET URLs the platforms fetch them by, and their removal. Credentials from .env
(reelline/config/credentials.py load_r2()).

Every call goes to `record` (reelline/publish/log.py CallLog): the action, the object key and the
result, never a key secret or a URL's signature.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from reelline.config import conf
from reelline.storage.blob import MAX_SIGNED_S, StorageError, no_record, strip_query

MAX_PRESIGN_S = MAX_SIGNED_S


class S3Store:
    name = "s3"

    def __init__(self, settings=None, client=None, record: Callable[..., None] = no_record):
        if client is None:
            import boto3
            from botocore.config import Config

            from reelline.config.credentials import load_r2

            settings = settings or load_r2()
            self.public_base = settings.public_url
            client = boto3.client(
                "s3",
                endpoint_url=settings.endpoint,
                region_name="auto",
                aws_access_key_id=settings.access_key_id,
                aws_secret_access_key=settings.secret_access_key,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=conf().publish.http_timeout_s,
                    read_timeout=conf().publish.upload_timeout_s,
                ),
            )
            self.bucket = settings.bucket
        else:
            self.bucket = settings.bucket if settings else "test-bucket"
            self.public_base = getattr(settings, "public_url", None)
        self.client, self.record = client, record

    def public_url(self, obj: str) -> str:
        """The object's URL on the bucket's public r2.dev domain (R2_PUBLIC_URL)."""
        if not self.public_base:
            raise StorageError("R2_PUBLIC_URL isn't set: enable the bucket's r2.dev public URL and put it in .env")
        return f"{self.public_base}/{obj}"

    def _call(self, action: str, method: str, obj: str, fn, **summary):
        try:
            result = fn()
        except Exception as exc:  # botocore's ClientError and connection errors
            status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
            self.record(
                action=action,
                method=method,
                endpoint=f"r2://{self.bucket}/{obj}",
                request=summary,
                status=status,
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
            raise StorageError(f"R2 {action} {obj}: {type(exc).__name__}: {exc}") from None
        status = (result or {}).get("ResponseMetadata", {}).get("HTTPStatusCode") if isinstance(result, dict) else None
        self.record(
            action=action, method=method, endpoint=f"r2://{self.bucket}/{obj}", request=summary, status=status or 200
        )
        return result

    def size(self, obj: str) -> int | None:
        """The object's size, None when it isn't there."""
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=obj)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                self.record(action="r2_head", method="HEAD", endpoint=f"r2://{self.bucket}/{obj}", status=404)
                return None
            self.record(
                action="r2_head",
                method="HEAD",
                endpoint=f"r2://{self.bucket}/{obj}",
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
            raise StorageError(f"R2 head {obj}: {exc}") from None
        self.record(action="r2_head", method="HEAD", endpoint=f"r2://{self.bucket}/{obj}", status=200)
        return head["ContentLength"]

    def put(self, key: str, src: Path | bytes, content_type: str = "application/octet-stream") -> None:
        """Store `src` unless an object of the same size is there already (a retry)."""
        if isinstance(src, bytes):
            self._call(
                "r2_upload",
                "PUT",
                key,
                lambda: self.client.put_object(Bucket=self.bucket, Key=key, Body=src, ContentType=content_type),
                bytes=len(src),
                content_type=content_type,
            )
            return
        if self.size(key) == src.stat().st_size:
            return
        self._call(
            "r2_upload",
            "PUT",
            key,
            lambda: self.client.upload_file(str(src), self.bucket, key, ExtraArgs={"ContentType": content_type}),
            bytes=src.stat().st_size,
            content_type=content_type,
        )

    def get(self, key: str) -> bytes:
        result = self._call("r2_get", "GET", key, lambda: self.client.get_object(Bucket=self.bucket, Key=key))
        return result["Body"].read()

    def exists(self, key: str) -> bool:
        return self.size(key) is not None

    def url(self, key: str, expires_s: int | None = None) -> str:
        """The public URL (`expires_s` None), or a GET URL valid `expires_s` (1 s .. 7 days)."""
        return self.public_url(key) if expires_s is None else self.presign(key, expires_s)

    def presign(self, obj: str, expires_s: int) -> str:
        expires_s = max(1, min(int(expires_s), MAX_PRESIGN_S))
        url = self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": obj}, ExpiresIn=expires_s
        )
        self.record(
            action="r2_presign",
            method="-",
            endpoint=f"r2://{self.bucket}/{obj}",
            request={"expires_s": expires_s, "url": strip_query(url)},
            status=None,
        )
        return url

    def delete(self, objs: list[str]) -> None:
        for obj in objs:
            self._call("r2_delete", "DELETE", obj, lambda o=obj: self.client.delete_object(Bucket=self.bucket, Key=o))

    def list(self, prefix: str) -> list[str]:
        result = self._call(
            "r2_list", "GET", prefix, lambda: self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        )
        return [o["Key"] for o in result.get("Contents", [])]
