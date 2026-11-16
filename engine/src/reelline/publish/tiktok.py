"""TikTok directly: the Content Posting API's Direct Post by URL (open.tiktokapis.com/v2, checked
2026-11): POST /post/publish/video/init/ with the account's access token, post_info (title = the caption,
privacy, cover frame, AI flag) and source_info PULL_FROM_URL (TikTok fetches the public URL: its domain
or URL prefix must be verified in the app); POST /post/publish/status/fetch/ says where it is
(PROCESSING_*, PUBLISH_COMPLETE, FAILED + fail_reason). There is no scheduling (the post goes out at
once; run the channel with publish.buffer_lead_h 0) and no delete call. The access token comes from
the refresh token (POST /oauth/token/) and lives a day; credentials in .env (publish.tiktok).
Every call goes to `record` with the endpoint only, never a token.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from reelline.config import conf
from reelline.publish.base import MediaKind, Placed, Post, PublisherError, Status
from reelline.storage.blob import no_record


class TikTokError(PublisherError):
    """A TikTok API call that failed (TikTok's code and message, no secrets)."""


class TikTokClient:
    def __init__(self, settings=None, http: httpx.Client | None = None, record: Callable[..., None] = no_record):
        if settings is None:
            from reelline.config.credentials import load_tiktok

            settings = load_tiktok()
        self.settings, self.record = settings, record
        self.http = http or httpx.Client(timeout=conf().publish.http_timeout_s)
        self._token: str | None = None

    def _redact(self, text: str) -> str:
        for secret in (self.settings.client_secret, self.settings.refresh_token, self._token):
            if secret:
                text = text.replace(secret, "<secret>")
        return text

    def _call(self, action: str, path: str, body: dict, *, form: bool = False, auth: bool = True) -> dict:
        url = f"{conf().publish.tiktok.api_url}/{path}"
        headers = {"Authorization": f"Bearer {self.access_token()}"} if auth else {}
        try:
            r = self.http.post(url, headers=headers, **({"data": body} if form else {"json": body}))
        except httpx.HTTPError as exc:
            msg = self._redact(f"{type(exc).__name__}: {exc}")[:500]
            self.record(action=action, method="POST", endpoint=url, channel="tiktok", error=msg)
            raise TikTokError(f"TikTok {action}: {msg}") from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        err = data.get("error") if isinstance(data, dict) else None
        code = (err or {}).get("code") if isinstance(err, dict) else None
        message = None
        if (
            (code and code != "ok")
            or r.status_code >= 400
            or (not err and "access_token" not in data and r.status_code != 200)
        ):
            message = self._redact(
                f"{code or 'error'}: {(err or {}).get('message') or data.get('error_description') or ''}"
                f" (HTTP {r.status_code})"
            )[:500]
        self.record(
            action=action,
            method="POST",
            endpoint=url,
            channel="tiktok",
            status=r.status_code,
            external_id=(data.get("data") or {}).get("publish_id") if isinstance(data.get("data"), dict) else None,
            error=message,
        )
        if message:
            raise TikTokError(f"TikTok {action}: {message}")
        return data

    def access_token(self) -> str:
        if self._token is None:
            s = self.settings
            body = self._call(
                "tiktok_token",
                "oauth/token/",
                {
                    "client_key": s.client_key,
                    "client_secret": s.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": s.refresh_token,
                },
                form=True,
                auth=False,
            )
            self._token = body["access_token"]
        return self._token

    def init(self, video_url: str, title: str, cover_ms: int | None) -> str:
        info = {
            "title": title,
            "privacy_level": conf().publish.tiktok.privacy,
            "is_aigc": bool(conf().publish.ai_generated),
        }
        if cover_ms is not None:
            info["video_cover_timestamp_ms"] = int(cover_ms)
        body = self._call(
            "tiktok_init",
            "post/publish/video/init/",
            {"post_info": info, "source_info": {"source": "PULL_FROM_URL", "video_url": video_url}},
        )
        return str(body["data"]["publish_id"])

    def status(self, publish_id: str) -> dict:
        return self._call("tiktok_status", "post/publish/status/fetch/", {"publish_id": publish_id}).get("data") or {}


class TikTokPublisher:
    name = "tiktok"
    channels = ("tiktok",)
    media: MediaKind = "public"
    schedules = False

    def __init__(self, client: TikTokClient | None = None, *, record: Callable[..., None] = no_record):
        self.client = client or TikTokClient(record=record)

    def publish(
        self,
        post: Post,
        *,
        existing: str | None = None,
        now: datetime | None = None,
        remember: Callable[[str], None] = lambda id: None,
    ) -> Placed:
        now = now or datetime.now(UTC)
        if post.when is not None and post.when - now > timedelta(minutes=conf().publish.now_within_min):
            raise TikTokError("TikTok's API can't schedule a post: publish it at its time (publish.buffer_lead_h 0)")
        if existing and self.status("tiktok", existing).state != "failed":
            return Placed(existing)
        publish_id = self.client.init(post.public_url, post.caption, post.cover_offset_ms)
        remember(publish_id)
        return Placed(publish_id)

    def status(self, channel: str, post_id: str) -> Status:
        data = self.client.status(post_id)
        state = data.get("status")
        if state == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
            return Status("published", url=f"https://www.tiktok.com/@/video/{ids[0]}" if ids else None, detail=state)
        if state == "FAILED":
            return Status("failed", error=f"TikTok: {data.get('fail_reason') or 'failed'}", detail=state)
        return Status("pending", detail=state)

    def delete(self, channel: str, post_id: str) -> None:
        raise TikTokError("TikTok's API can't delete a post")

    def exists(self, channel: str, post_id: str) -> bool:
        return True
