"""YouTube's APIs: the YouTubePublisher adapter (resumable videos.insert, videos.list, videos.delete
with the channel owner's OAuth token; its refresh token needs the youtube.upload scope), the stats
(reelline/stats/youtube.py; read-only) and the links comment (reelline/pipeline/comments.py:
commentThreads.insert, with the OAuth token).

Data API v3 (developers.google.com/youtube/v3, checked 2026-10-02) with
YOUTUBE_API_KEY: videos.list part=statistics,snippet (up to 50 ids, 1 quota unit)
and channels.list part=statistics (subscriberCount, 1 unit); 10,000 units a day.

Analytics API v2 (developers.google.com/youtube/analytics) with the channel owner's
OAuth refresh token (scope yt-analytics.readonly; YOUTUBE_CLIENT_ID / _SECRET /
_REFRESH_TOKEN, made once by python -m reelline.pipeline.stats_auth youtube): reports.query
ids=channel==MINE, dimensions=video, filters=video==a,b,... for views,
averageViewDuration, averageViewPercentage, shares, subscribersGained. Its numbers
lag about a day or two. The retention curve (dimension elapsedVideoTimeRatio, metrics
audienceWatchRatio and relativeRetentionPerformance, checked 2026-10-25) takes one video
per call (several ids in the filter come back merged): 100 rows, ratio 0.01 .. 1.00;
a video with too few views has no rows.

Every call goes to `record` (reelline/pipeline/publish.py CallLog) with the endpoint only:
never the key, the client secret or a token.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from reelline.config import conf
from reelline.publish.base import MediaKind, Placed, Post, PublisherError, Status
from reelline.storage.blob import no_record


class YouTubeError(PublisherError):
    """A YouTube API call that failed (Google's message, no secrets)."""


class YouTubeClient:
    def __init__(self, settings=None, http: httpx.Client | None = None, record: Callable[..., None] = no_record):
        if settings is None:
            from reelline.config.credentials import load_youtube

            settings = load_youtube()
        self.settings, self.record = settings, record
        self.http = http or httpx.Client(timeout=conf().steps.analytics.http_timeout_s)
        self._access_token: str | None = None

    def _secrets(self) -> list[str]:
        s = self.settings
        return [x for x in (s.api_key, s.client_secret, s.refresh_token, self._access_token) if x]

    def _redact(self, text: str) -> str:
        for secret in self._secrets():
            text = text.replace(secret, "<secret>")
        return text

    def _call(
        self,
        action: str,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        headers: dict | None = None,
        json: dict | None = None,
        content=None,
        response: bool = False,
    ):
        try:
            r = self.http.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                json=json,
                **({"content": content} if content is not None else {}),
            )
        except httpx.HTTPError as exc:
            msg = self._redact(f"{type(exc).__name__}: {exc}")[:500]
            self.record(action=action, method=method, endpoint=url, channel="youtube", error=msg)
            raise YouTubeError(f"YouTube {action}: {msg}") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        message = None
        if err or r.status_code >= 400:
            if isinstance(err, dict):
                message = f"{err.get('message') or err.get('status')} (HTTP {r.status_code})"
            else:
                message = f"{err or 'error'}: {body.get('error_description', '')} (HTTP {r.status_code})"
            message = self._redact(message)[:500]
        self.record(action=action, method=method, endpoint=url, channel="youtube", status=r.status_code, error=message)
        if message:
            raise YouTubeError(f"YouTube {action}: {message}")
        return (body, r) if response else body

    # ---- Data API (key) -------------------------------------------------------------------

    def videos(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            out += self._call(
                "stats_yt_videos",
                "GET",
                f"{conf().steps.analytics.youtube_data_url}/videos",
                params={
                    "part": "statistics,snippet,contentDetails",
                    "id": ",".join(ids[i : i + 50]),
                    "key": self.settings.api_key,
                },
            ).get("items", [])
        return out

    def channel(self, channel_id: str) -> dict | None:
        items = self._call(
            "stats_yt_channel",
            "GET",
            f"{conf().steps.analytics.youtube_data_url}/channels",
            params={"part": "statistics", "id": channel_id, "key": self.settings.api_key},
        ).get("items")
        return items[0] if items else None

    # ---- Analytics API (OAuth) ----------------------------------------------------------------

    def access_token(self) -> str:
        if self._access_token is None:
            s = self.settings
            body = self._call(
                "stats_yt_token",
                "POST",
                conf().steps.analytics.google_token_url,
                data={
                    "client_id": s.client_id,
                    "client_secret": s.client_secret,
                    "refresh_token": s.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            self._access_token = body["access_token"]
        return self._access_token

    def video_report(self, ids: list[str], start: date, end: date) -> list[dict]:
        """One dict per video with data: {"video", "views", "averageViewDuration", ...}."""
        metrics = "views,averageViewDuration,averageViewPercentage,shares,subscribersGained"
        out = []
        for i in range(0, len(ids), 200):
            body = self._call(
                "stats_yt_analytics",
                "GET",
                conf().steps.analytics.youtube_analytics_url,
                params={
                    "ids": "channel==MINE",
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": metrics,
                    "dimensions": "video",
                    "filters": "video==" + ",".join(ids[i : i + 200]),
                },
                headers={"Authorization": f"Bearer {self.access_token()}"},
            )
            names = [h["name"] for h in body.get("columnHeaders", [])]
            out += [dict(zip(names, row, strict=False)) for row in body.get("rows") or []]
        return out

    def retention(self, video_id: str, start: date, end: date) -> list[list[float]]:
        """The video's retention curve: [[elapsed ratio, audienceWatchRatio,
        relativeRetentionPerformance], ...] by ratio; [] when YouTube has too few views to give one."""
        body = self._call(
            "stats_yt_retention",
            "GET",
            conf().steps.analytics.youtube_analytics_url,
            params={
                "ids": "channel==MINE",
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metrics": "audienceWatchRatio,relativeRetentionPerformance",
                "dimensions": "elapsedVideoTimeRatio",
                "filters": f"video=={video_id}",
            },
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )
        names = [h["name"] for h in body.get("columnHeaders", [])]
        order = [
            names.index(n)
            for n in ("elapsedVideoTimeRatio", "audienceWatchRatio", "relativeRetentionPerformance")
            if n in names
        ]
        return sorted([[row[i] for i in order] for row in body.get("rows") or []])

    # ---- uploads (OAuth, scope youtube.upload) ---------------------------------------------

    def upload(self, path: Path, metadata: dict, parts: str = "snippet,status") -> dict:
        """Resumable videos.insert: the session, then the file in one PUT. Returns the video resource."""
        auth = {"Authorization": f"Bearer {self.access_token()}"}
        _, started = self._call(
            "yt_upload_start",
            "POST",
            f"{conf().publish.youtube_upload_url}/videos",
            params={"uploadType": "resumable", "part": parts},
            json=metadata,
            response=True,
            headers={**auth, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(path.stat().st_size)},
        )
        session = started.headers.get("location")
        if not session:
            raise YouTubeError("YouTube yt_upload_start: no upload session in the reply")
        with path.open("rb") as fh:
            return self._call(
                "yt_upload",
                "PUT",
                session,
                headers={**auth, "Content-Type": "video/mp4"},
                content=iter(lambda: fh.read(1 << 20), b""),
            )

    def video_status(self, video_id: str) -> dict | None:
        items = self._call(
            "yt_video_status",
            "GET",
            f"{conf().steps.analytics.youtube_data_url}/videos",
            params={"part": "status,snippet", "id": video_id},
            headers={"Authorization": f"Bearer {self.access_token()}"},
        ).get("items")
        return items[0] if items else None

    def delete_video(self, video_id: str) -> None:
        self._call(
            "yt_delete",
            "DELETE",
            f"{conf().steps.analytics.youtube_data_url}/videos",
            params={"id": video_id},
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )


class YouTubePublisher:
    """YouTube Shorts directly (no Buffer): the file is uploaded from disk; a future time is the video
    private with status.publishAt (YouTube makes it public then)."""

    name = "youtube"
    channels = ("youtube",)
    media: MediaKind = "file"
    schedules = True

    def __init__(self, client: YouTubeClient | None = None, *, record: Callable[..., None] = no_record):
        self.client = client or YouTubeClient(record=record)

    def publish(
        self,
        post: Post,
        *,
        existing: str | None = None,
        now: datetime | None = None,
        remember: Callable[[str], None] = lambda id: None,
    ) -> Placed:
        if existing:
            found = self.client.video_status(existing)
            if found is not None and (found.get("status") or {}).get("uploadStatus") not in ("failed", "rejected"):
                return Placed(existing)
            if found is not None:
                self.client.delete_video(existing)
        yt = dict(conf().publish.youtube)
        status = {
            "privacyStatus": yt.get("privacy", "public"),
            "selfDeclaredMadeForKids": bool(yt.get("madeForKids")),
            "containsSyntheticMedia": bool(conf().publish.ai_generated),
        }
        if post.when is not None:
            status.update(
                privacyStatus="private", publishAt=post.when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            )
        body = {
            "snippet": {
                "title": (post.title or post.caption.splitlines()[0])[:100],
                "description": post.caption,
                "categoryId": str(yt.get("categoryId", "27")),
            },
            "status": status,
        }
        video = self.client.upload(post.path, body)
        remember(video["id"])
        return Placed(video["id"])

    def status(self, channel: str, post_id: str) -> Status:
        v = self.client.video_status(post_id)
        if v is None:
            return Status("gone", error="the video is gone from YouTube")
        st = v.get("status") or {}
        if st.get("uploadStatus") in ("failed", "rejected"):
            return Status(
                "failed",
                error=f"YouTube: upload {st['uploadStatus']} ({st.get('failureReason') or st.get('rejectionReason')})",
                detail=st["uploadStatus"],
            )
        if st.get("privacyStatus") == "public" and not st.get("publishAt"):
            return Status("published", url=f"https://www.youtube.com/shorts/{post_id}", detail="public")
        return Status("pending", detail=st.get("privacyStatus"))

    def delete(self, channel: str, post_id: str) -> None:
        self.client.delete_video(post_id)

    def exists(self, channel: str, post_id: str) -> bool:
        return self.client.video_status(post_id) is not None
