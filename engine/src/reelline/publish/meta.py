"""The Facebook Page's reels and Instagram Reels through the Graph API: the MetaPublisher adapter
(reelline/publish/base.py) over FacebookClient.

Reels Publishing API (developers.facebook.com/docs/video-api/guides/reels-publishing,
checked 2026-10-01; publish.graph_url is v26.0): POST /{page}/video_reels
upload_phase=start gives the video id and its upload URL; POST that URL with
"Authorization: OAuth <page token>" and a "file_url" header makes Meta fetch the file
(a hosted URL, ours a presigned R2 GET); a custom cover is POST /{video}/thumbnails
(source, is_preferred); upload_phase=finish with video_state SCHEDULED +
scheduled_publish_time (unix, more than 10 min and at most 29 days ahead) or PUBLISHED,
the description (the caption) and is_ai_generated. GET /{video}?fields=status says
where it is (status.publishing_phase.publish_status: scheduled, published, error),
DELETE /{video} removes it, POST /{video}/comments comments as the Page (Graph API
comments can't be pinned).

check_page() refuses any token whose Page isn't publish.facebook_page_id /
"facebook_page_name", so nothing is ever posted to the wrong Page. Every call goes to
`record` (reelline/pipeline/publish.py CallLog): the endpoint without the token, a redacted
summary, the status, the video id and Meta's error.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from reelline.config import conf
from reelline.publish.base import MediaKind, Placed, Post, PublisherError, Status
from reelline.storage.blob import no_record, strip_query


class FacebookError(PublisherError):
    """A Graph API call that failed (Meta's message), or the wrong Page."""


class FacebookClient:
    def __init__(self, settings=None, http: httpx.Client | None = None, record: Callable[..., None] = no_record):
        if settings is None:
            from reelline.config.credentials import load_meta

            settings = load_meta()
        self.settings, self.record = settings, record
        self.http = http or httpx.Client(timeout=conf().publish.upload_timeout_s)
        self.page_id = settings.page_id

    def _call(
        self,
        action: str,
        method: str,
        url: str,
        *,
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        files: dict | None = None,
        summary: dict | None = None,
        external_id: str | None = None,
    ) -> dict:
        token = self.settings.page_token
        endpoint = strip_query(url)
        try:
            if headers and "Authorization" in headers:
                r = self.http.request(method, url, headers=headers, data=data)
            else:
                r = self.http.request(
                    method, url, params={"access_token": token, **(params or {})}, data=data, files=files
                )
        except httpx.HTTPError as exc:
            self.record(
                action=action,
                method=method,
                endpoint=endpoint,
                request=summary,
                channel="facebook",
                external_id=external_id,
                error=f"{type(exc).__name__}: {exc}"[:500].replace(token, "<token>"),
            )
            raise FacebookError(f"Facebook {action}: {type(exc).__name__}") from None
        try:
            body = r.json()
        except ValueError:
            body = {"error": {"message": f"HTTP {r.status_code}, non-JSON reply"}}
        if not isinstance(body, dict):
            body = {"result": body}
        err = body.get("error")
        message = None
        if err:
            message = (
                f"{err.get('message')} (code {err.get('code')}"
                + (f"/{err.get('error_subcode')}" if err.get("error_subcode") else "")
                + ")"
            ).replace(token, "<token>")
        self.record(
            action=action,
            method=method,
            endpoint=endpoint,
            request=summary,
            channel="facebook",
            status=r.status_code,
            external_id=body.get("video_id") or body.get("id") or external_id,
            error=message,
        )
        if err or r.status_code >= 400:
            raise FacebookError(f"Facebook {action}: {message or f'HTTP {r.status_code}'}")
        return body

    def graph(self, path: str) -> str:
        return f"{conf().publish.graph_url}/{path}"

    # ---- checks ------------------------------------------------------------------------

    def check_page(self) -> dict:
        """The token's Page must be the configured one (id and name); returns
        {"id", "name", "data_access_expires_at"} (a datetime or None)."""
        me = self._call("fb_page_check", "GET", self.graph("me"), params={"fields": "id,name"})
        want = (conf().publish.facebook_page_id, conf().publish.facebook_page_name)
        if (str(me.get("id")), me.get("name")) != want or self.page_id != want[0]:
            raise FacebookError(
                f"Facebook token is for Page {me.get('id')} {me.get('name')!r} (META_PAGE_ID "
                f"{self.page_id}), expected {want[0]} {want[1]!r}: refusing to post"
            )
        expires = None
        try:
            d = self._call(
                "fb_debug_token",
                "GET",
                self.graph("debug_token"),
                params={
                    "input_token": self.settings.page_token,
                    "access_token": f"{self.settings.app_id}|{self.settings.app_secret}",
                },
            )["data"]
            if d.get("data_access_expires_at"):
                expires = datetime.fromtimestamp(d["data_access_expires_at"], UTC)
        except (FacebookError, KeyError):
            pass
        return {"id": me["id"], "name": me["name"], "data_access_expires_at": expires}

    # ---- the reel ----------------------------------------------------------------------

    def start(self) -> tuple[str, str]:
        """(video id, upload URL)."""
        body = self._call(
            "fb_start",
            "POST",
            self.graph(f"{self.page_id}/video_reels"),
            data={"upload_phase": "start"},
            summary={"upload_phase": "start"},
        )
        return (
            str(body["video_id"]),
            body.get("upload_url")
            or f"https://rupload.facebook.com/video-upload/{conf().publish.graph_url.rsplit('/', 1)[-1]}/{body['video_id']}",
        )

    def upload(self, video_id: str, upload_url: str, file_url: str) -> None:
        self._call(
            "fb_upload",
            "POST",
            upload_url,
            external_id=video_id,
            headers={"Authorization": f"OAuth {self.settings.page_token}", "file_url": file_url},
            summary={"file_url": strip_query(file_url)},
        )

    def status(self, video_id: str) -> dict:
        return (
            self._call("fb_status", "GET", self.graph(video_id), params={"fields": "status"}, external_id=video_id).get(
                "status"
            )
            or {}
        )

    def thumbnail(self, video_id: str, cover: Path) -> None:
        with cover.open("rb") as fh:
            self._call(
                "fb_thumbnail",
                "POST",
                self.graph(f"{video_id}/thumbnails"),
                external_id=video_id,
                data={"is_preferred": "true"},
                files={"source": (cover.name, fh, "image/png")},
                summary={"source": cover.name, "is_preferred": True},
            )

    def finish(self, video_id: str, description: str, publish_at: datetime | None) -> dict:
        """Schedule for `publish_at` (aware), or publish now when None."""
        data = {
            "upload_phase": "finish",
            "video_id": video_id,
            "description": description,
            "video_state": "SCHEDULED" if publish_at else "PUBLISHED",
        }
        if publish_at:
            data["scheduled_publish_time"] = str(int(publish_at.timestamp()))
        if conf().publish.ai_generated:
            data["is_ai_generated"] = "true"
        return self._call(
            "fb_finish",
            "POST",
            self.graph(f"{self.page_id}/video_reels"),
            data=data,
            external_id=video_id,
            summary={k: v for k, v in data.items()},
        )

    def permalink(self, video_id: str) -> str | None:
        try:
            body = self._call(
                "fb_permalink", "GET", self.graph(video_id), params={"fields": "permalink_url"}, external_id=video_id
            )
        except FacebookError:
            return None
        link = body.get("permalink_url")
        if link and link.startswith("/"):
            link = "https://www.facebook.com" + link
        return link

    def delete(self, video_id: str) -> None:
        self._call("fb_delete", "DELETE", self.graph(video_id), external_id=video_id)

    def exists(self, video_id: str) -> bool:
        try:
            self._call("fb_exists", "GET", self.graph(video_id), params={"fields": "id"}, external_id=video_id)
        except FacebookError:
            return False
        return True

    def comment(self, video_id: str, message: str) -> str:
        body = self._call(
            "fb_comment",
            "POST",
            self.graph(f"{video_id}/comments"),
            data={"message": message},
            external_id=video_id,
            summary={"message": message},
        )
        return str(body.get("id"))

    # ---- Instagram Reels (content publishing: container, then publish) -------------------

    def ig_user_id(self) -> str:
        body = self._call(
            "ig_account", "GET", self.graph(self.page_id), params={"fields": "instagram_business_account"}
        )
        account = (body.get("instagram_business_account") or {}).get("id")
        if not account:
            raise FacebookError("Instagram: the Page has no linked Instagram professional account")
        return str(account)

    def ig_container(self, ig_user: str, video_url: str, caption: str, offset_ms: int | None) -> str:
        data = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"}
        if offset_ms is not None:
            data["thumb_offset"] = str(int(offset_ms))
        body = self._call(
            "ig_container",
            "POST",
            self.graph(f"{ig_user}/media"),
            data=data,
            summary={**data, "video_url": strip_query(video_url)},
        )
        return str(body["id"])

    def ig_container_status(self, container: str) -> str:
        """IN_PROGRESS | FINISHED | ERROR | EXPIRED | PUBLISHED."""
        return str(
            self._call(
                "ig_container_status",
                "GET",
                self.graph(container),
                params={"fields": "status_code"},
                external_id=container,
            ).get("status_code")
        )

    def ig_publish(self, ig_user: str, container: str) -> str:
        body = self._call(
            "ig_publish",
            "POST",
            self.graph(f"{ig_user}/media_publish"),
            data={"creation_id": container},
            external_id=container,
            summary={"creation_id": container},
        )
        return str(body["id"])

    def ig_permalink(self, media_id: str) -> str | None:
        try:
            return self._call(
                "ig_permalink", "GET", self.graph(media_id), params={"fields": "permalink"}, external_id=media_id
            ).get("permalink")
        except FacebookError:
            return None

    def reels(self) -> list[dict]:
        return self._call(
            "fb_list",
            "GET",
            self.graph(f"{self.page_id}/video_reels"),
            params={"fields": "id,description,updated_time"},
        ).get("data", [])


def publish_status(status: dict) -> str:
    """scheduled | published | error | processing (still uploading / processing) | draft."""
    if status.get("video_status") in ("error", "upload_failed", "expired"):
        return "error"
    for phase in ("uploading_phase", "processing_phase", "publishing_phase"):
        if (status.get(phase) or {}).get("status") == "error":
            return "error"
    publishing = status.get("publishing_phase") or {}
    if publishing.get("publish_status") in ("scheduled", "published", "draft"):
        return publishing["publish_status"]
    return "processing"


def error_text(status: dict) -> str:
    for phase in ("uploading_phase", "processing_phase", "publishing_phase"):
        p = status.get(phase) or {}
        err = p.get("error") or (p.get("errors") or [None])[0]
        if err:
            return f"{phase}: {err.get('message') if isinstance(err, dict) else err}"
    return f"video_status {status.get('video_status')}"


class MetaPublisher:
    """Facebook Page reels (scheduled by Meta) and Instagram Reels (the Graph API cannot schedule them:
    a future time is refused, so run the channel with publish.buffer_lead_h 0 or post now). Both fetch
    the file from a signed URL."""

    name = "meta"
    channels = ("facebook", "instagram")
    media: MediaKind = "signed"
    schedules = True

    def __init__(
        self,
        client: FacebookClient | None = None,
        *,
        record: Callable[..., None] = no_record,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client or FacebookClient(record=record)
        self.sleep = sleep

    def publish(
        self,
        post: Post,
        *,
        existing: str | None = None,
        now: datetime | None = None,
        remember: Callable[[str], None] = lambda id: None,
    ) -> Placed:
        now = now or datetime.now(UTC)
        if post.channel == "instagram":
            return self._instagram(post, existing, now, remember)
        if post.channel != "facebook":
            raise PublisherError(f"Meta doesn't publish {post.channel!r}")
        return self._facebook(post, existing, now, remember)

    def _facebook(self, post: Post, existing: str | None, now: datetime, remember) -> Placed:
        fb, warnings = self.client, []
        info = fb.check_page()
        expires = info.get("data_access_expires_at")
        if expires and expires - now < timedelta(days=conf().publish.facebook_data_access_warn_days):
            warnings.append(f"Facebook token's data access expires {expires.date()}: re-authorize the app")
        if post.when is not None and post.when - now > timedelta(days=29):
            raise FacebookError(f"Facebook schedules at most 29 days ahead; the slot is {post.when.date()}")
        if existing:
            try:
                state = publish_status(fb.status(existing))
            except FacebookError:
                state = "gone"
            if state in ("scheduled", "published"):
                return Placed(existing, warnings=warnings)
            if state != "gone":
                fb.delete(existing)
        video_id, upload_url = fb.start()
        remember(video_id)  # stored before anything else happens
        fb.upload(video_id, upload_url, post.signed_url)
        waited = 0.0
        while True:
            status = fb.status(video_id)
            uploading = (status.get("uploading_phase") or {}).get("status")
            if publish_status(status) == "error":
                raise FacebookError(f"Facebook upload: {error_text(status)}")
            if uploading == "complete" or status.get("video_status") in ("upload_complete", "processing", "ready"):
                break
            if waited >= conf().publish.facebook_upload_wait_s:
                raise FacebookError(f"Facebook upload: not complete after {waited:.0f}s ({uploading})")
            self.sleep(conf().publish.facebook_upload_poll_s)
            waited += conf().publish.facebook_upload_poll_s
        if post.cover is not None:
            try:
                fb.thumbnail(video_id, post.cover)
            except FacebookError as exc:
                warnings.append(f"Facebook cover not set ({exc}); the reel keeps Meta's frame")
        fb.finish(video_id, post.caption, post.when)
        return Placed(video_id, warnings=warnings)

    def _instagram(self, post: Post, existing: str | None, now: datetime, remember) -> Placed:
        fb = self.client
        if post.when is not None and post.when - now > timedelta(minutes=conf().publish.now_within_min):
            raise FacebookError(
                "Instagram's API can't schedule a reel: publish it at its time (publish.buffer_lead_h 0)"
            )
        if existing and not existing.startswith("c:"):
            if fb.exists(existing):
                return Placed(existing)
            existing = None
        ig = fb.ig_user_id()
        container = (
            existing[2:] if existing else fb.ig_container(ig, post.signed_url, post.caption, post.cover_offset_ms)
        )
        remember(f"c:{container}")
        waited = 0.0
        while True:
            code = fb.ig_container_status(container)
            if code in ("ERROR", "EXPIRED"):
                raise FacebookError(f"Instagram: the upload is {code}")
            if code == "FINISHED":
                break
            if code == "PUBLISHED":
                raise FacebookError("Instagram: the container was published already; check the account")
            if waited >= conf().publish.facebook_upload_wait_s:
                raise FacebookError(f"Instagram: not processed after {waited:.0f}s ({code})")
            self.sleep(conf().publish.facebook_upload_poll_s)
            waited += conf().publish.facebook_upload_poll_s
        media_id = fb.ig_publish(ig, container)
        remember(media_id)
        return Placed(media_id, "published", url=fb.ig_permalink(media_id))

    def status(self, channel: str, post_id: str) -> Status:
        fb = self.client
        if channel == "instagram":
            if post_id.startswith("c:"):
                code = fb.ig_container_status(post_id[2:])
                if code in ("ERROR", "EXPIRED"):
                    return Status("failed", error=f"Instagram: the upload is {code}", detail=code)
                return Status("pending", detail=code)
            return (
                Status("published", url=fb.ig_permalink(post_id), detail="published")
                if fb.exists(post_id)
                else Status("gone", error="the post is gone from Instagram")
            )
        status = fb.status(post_id)
        state = publish_status(status)
        if state == "published":
            return Status(
                "published", url=fb.permalink(post_id) or f"https://www.facebook.com/reel/{post_id}", detail=state
            )
        if state == "error":
            return Status("failed", error=error_text(status), detail=state)
        return Status("pending", detail=state)

    def delete(self, channel: str, post_id: str) -> None:
        if channel == "instagram":
            raise FacebookError("Instagram's API can't delete a post")
        self.client.delete(post_id)

    def exists(self, channel: str, post_id: str) -> bool:
        return self.client.exists(post_id)
