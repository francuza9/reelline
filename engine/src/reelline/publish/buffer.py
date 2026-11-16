"""Buffer's API for Instagram, TikTok and YouTube (reelline/pipeline/publish.py).

The GraphQL API at publish.buffer_url (developers.buffer.com, checked 2026-10-01):
POST {query, variables} with "Authorization: Bearer <BUFFER_API_KEY>". createPost
(schedulingType automatic, mode customScheduled + dueAt, or shareNow; never saveToDraft)
with the video by URL; post(id) for its status (scheduled, sending, sent, error),
error message and externalLink; deletePost; posts(filter) to find an existing post on
retry and to count a channel's queue against the organization's scheduledPosts limit.
Buffer fetches the media when the post goes out, so the URL must live past dueAt.

Mutations return a union: the success type or a MutationError with a message, which
raises BufferError. Every call goes to `record` (reelline/pipeline/publish.py CallLog) with a
redacted summary: no API key, no URL signature.

Every reply's RateLimit header (the 15-minute, daily and 30-day windows) goes to
`on_limits`: reelline/pipeline/stats.py keeps the last one, so stats never eat the budget
publishing needs. post_metrics() is the read stats use (Post.metrics, refreshed by
Buffer about daily: developers.buffer.com/guides/post-metrics, checked 2026-10-02).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from reelline.config import conf
from reelline.publish.base import MediaKind, Placed, Post, PublisherError, QueueFull, Status
from reelline.storage.blob import no_record, strip_query

POST_FIELDS = """id status channelId channelService dueAt sentAt createdAt text externalLink shareMode schedulingType
    isCustomScheduled allowedActions error { message rawError }
    assets { ... on VideoAsset { source thumbnail video { durationMs width height thumbnailOffset } } }
    metadata { __typename
      ... on InstagramPostMetadata { type shouldShareToFeed firstComment isAiGenerated }
      ... on YoutubePostMetadata { title privacy madeForKids notifySubscribers isAiGenerated category { categoryId title } }
      ... on TiktokPostMetadata { isAiGenerated } }"""


class BufferError(PublisherError):
    """A Buffer call that failed or was refused (the message says why)."""


class BufferQueueFull(QueueFull, BufferError):
    """The channel's queue is at the plan's scheduled-posts limit."""


def summarize(variables: dict) -> dict:
    """The request with signed URLs cut to their path (for the call log)."""

    def walk(v):
        if isinstance(v, dict):
            return {
                k: (strip_query(x) if k in ("url", "thumbnailUrl") and isinstance(x, str) else walk(x))
                for k, x in v.items()
            }
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(variables)


def parse_ratelimit(header: str | None) -> dict[str, dict]:
    """'"100-in-15min"; r=99; t=900, "250-in-1day"; r=162; t=9906, ...' ->
    {"15min": {"limit": 100, "remaining": 99, "reset_s": 900}, ...}."""
    out = {}
    for m in re.finditer(r'"(\d+)-in-(\w+)"\s*;\s*r=(\d+)\s*;\s*t=(\d+)', header or ""):
        out[m.group(2)] = {"limit": int(m.group(1)), "remaining": int(m.group(3)), "reset_s": int(m.group(4))}
    return out


class BufferClient:
    def __init__(
        self,
        settings=None,
        http: httpx.Client | None = None,
        record: Callable[..., None] = no_record,
        on_limits: Callable[[dict], None] | None = None,
    ):
        if settings is None:
            from reelline.config.credentials import load_buffer

            settings = load_buffer()
        self.settings, self.record, self.on_limits = settings, record, on_limits
        self.http = http or httpx.Client(timeout=conf().publish.http_timeout_s)
        self.channels = {
            "instagram": settings.channel_instagram,
            "tiktok": settings.channel_tiktok,
            "youtube": settings.channel_youtube,
        }

    def gql(self, action: str, query: str, variables: dict | None = None, *, channel: str | None = None) -> dict:
        variables = variables or {}
        try:
            r = self.http.post(
                conf().publish.buffer_url,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )
        except httpx.HTTPError as exc:
            self.record(
                action=action,
                method="POST",
                endpoint=conf().publish.buffer_url,
                channel=channel,
                request=summarize(variables),
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
            raise BufferError(f"Buffer {action}: {type(exc).__name__}: {exc}") from None
        limits = parse_ratelimit(r.headers.get("ratelimit"))
        if limits and self.on_limits is not None:
            with contextlib.suppress(Exception):  # bookkeeping only: never fails the call
                self.on_limits(limits)
        try:
            body = r.json()
        except ValueError:
            body = {}
        errors = body.get("errors") or []
        error = "; ".join(str(e.get("message")) for e in errors) or (
            None if r.status_code == 200 else f"HTTP {r.status_code}"
        )
        external = _external_id(body.get("data") or {})
        error = error or _mutation_error(body.get("data") or {})
        self.record(
            action=action,
            method="POST",
            endpoint=conf().publish.buffer_url,
            channel=channel,
            request=summarize(variables),
            status=r.status_code,
            external_id=external,
            error=error,
        )
        if r.status_code == 429:
            raise BufferError(f"Buffer {action}: rate limited (retry after {r.headers.get('retry-after', '?')} s)")
        if errors or r.status_code != 200:
            raise BufferError(f"Buffer {action}: {error}")
        return body["data"]

    # ---- reads -----------------------------------------------------------------------

    def scheduled_limit(self) -> int:
        data = self.gql("buffer_limits", "query { account { organizations { id limits { scheduledPosts } } } }")
        for org in data["account"]["organizations"]:
            if org["id"] == self.settings.org_id:
                return org["limits"]["scheduledPosts"]
        raise BufferError(f"Buffer organization {self.settings.org_id} not on this API key's account")

    def find_posts(
        self,
        channel: str,
        statuses: list[str],
        *,
        due: tuple[str, str] | None = None,
        created: tuple[str, str] | None = None,
        first: int = 100,
    ) -> list[dict]:
        """The channel's posts with these statuses (optionally due / created in [start, end])."""
        flt: dict = {"channelIds": [self.channels[channel]], "status": statuses}
        if due:
            flt["dueAt"] = {"start": due[0], "end": due[1]}
        if created:
            flt["createdAt"] = {"start": created[0], "end": created[1]}
        out, after = [], None
        while True:
            data = self.gql(
                "buffer_list",
                f"""query($i: PostsInput!, $first: Int, $after: String) {{
                posts(first: $first, after: $after, input: $i) {{ edges {{ node {{ {POST_FIELDS} }} }}
                pageInfo {{ hasNextPage endCursor }} }} }}""",
                {"i": {"organizationId": self.settings.org_id, "filter": flt}, "first": first, "after": after},
                channel=channel,
            )
            posts = data["posts"]
            out += [e["node"] for e in posts.get("edges") or []]
            if not posts["pageInfo"]["hasNextPage"]:
                return out
            after = posts["pageInfo"]["endCursor"]

    def queued(self, channel: str) -> int:
        return len(self.find_posts(channel, ["scheduled"]))

    def get_post(self, post_id: str, channel: str | None = None) -> dict | None:
        """The post, None when Buffer doesn't have it (deleted)."""
        try:
            return self.gql(
                "buffer_get",
                f"query($i: PostInput!) {{ post(input: $i) {{ {POST_FIELDS} }} }}",
                {"i": {"id": post_id}},
                channel=channel,
            )["post"]
        except BufferError as exc:
            if "not found" in str(exc).lower() or "notfound" in str(exc).lower():
                return None
            raise

    def post_metrics(self, channels: list[str], created: tuple[str, str]) -> list[dict]:
        """Sent posts on these channels created in [start, end], with their metrics
        (one call per 100 posts)."""
        flt = {
            "channelIds": [self.channels[c] for c in channels],
            "status": ["sent"],
            "createdAt": {"start": created[0], "end": created[1]},
        }
        out, after = [], None
        while True:
            data = self.gql(
                "stats_buffer_metrics",
                """query($i: PostsInput!, $first: Int, $after: String) {
                posts(first: $first, after: $after, input: $i) { edges { node { id channelService sentAt
                externalLink metricsUpdatedAt metrics { type unit value } } } pageInfo { hasNextPage endCursor } } }""",
                {"i": {"organizationId": self.settings.org_id, "filter": flt}, "first": 100, "after": after},
            )
            posts = data["posts"]
            out += [e["node"] for e in posts.get("edges") or []]
            if not posts["pageInfo"]["hasNextPage"]:
                return out
            after = posts["pageInfo"]["endCursor"]

    # ---- writes ----------------------------------------------------------------------

    def create_post(self, channel: str, post_input: dict) -> dict:
        data = self.gql(
            "buffer_create",
            f"""mutation($i: CreatePostInput!) {{ createPost(input: $i) {{
            __typename ... on PostActionSuccess {{ post {{ {POST_FIELDS} }} }} ... on MutationError {{ message }} }} }}""",
            {"i": post_input},
            channel=channel,
        )["createPost"]
        if data["__typename"] != "PostActionSuccess":
            exc = BufferQueueFull if data["__typename"] == "LimitReachedError" else BufferError
            raise exc(f"Buffer createPost: {data['__typename']}: {data.get('message')}")
        return data["post"]

    def delete_post(self, post_id: str, channel: str | None = None) -> None:
        data = self.gql(
            "buffer_delete",
            """mutation($i: DeletePostInput!) { deletePost(input: $i) {
            __typename ... on DeletePostSuccess { id } ... on MutationError { message } } }""",
            {"i": {"id": post_id}},
            channel=channel,
        )["deletePost"]
        if data["__typename"] != "DeletePostSuccess":
            raise BufferError(f"Buffer deletePost {post_id}: {data['__typename']}: {data.get('message')}")


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="seconds")


def _same_post(post: dict, text: str, due_iso: str | None) -> bool:
    if (post.get("text") or "").strip() != text.strip():
        return False
    if due_iso is None or not post.get("dueAt"):
        return True
    return (
        abs(
            (
                datetime.fromisoformat(post["dueAt"].replace("Z", "+00:00"))
                - datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
            ).total_seconds()
        )
        < 120
    )


class BufferPublisher:
    """Instagram, TikTok and YouTube through Buffer's queue (it fetches the file from the public URL)."""

    name = "buffer"
    channels = ("instagram", "tiktok", "youtube")
    media: MediaKind = "public"
    schedules = True

    def __init__(
        self,
        client: BufferClient | None = None,
        *,
        record: Callable[..., None] = no_record,
        on_limits: Callable[[dict], None] | None = None,
    ):
        self.client = client or BufferClient(record=record, on_limits=on_limits)
        self._limit: int | None = None

    def publish(
        self,
        post: Post,
        *,
        existing: str | None = None,
        now: datetime | None = None,
        remember: Callable[[str], None] = lambda id: None,
    ) -> Placed:
        ch, b = post.channel, self.client
        now = now or datetime.now(UTC)
        due_iso = _iso(post.when) if post.when else None
        # Something there already? (a retry, a double tap, a crash after create)
        found = b.get_post(existing, ch) if existing else None
        if found is not None and found["status"] not in ("scheduled", "sending", "sent"):
            if "deletePost" in (found.get("allowedActions") or []):
                b.delete_post(found["id"], ch)
            found = None
        if found is None:
            listed = b.find_posts(
                ch,
                ["scheduled", "sending", "sent", "draft", "needs_approval"],
                created=(_stamp(now - timedelta(days=1)), _stamp(now + timedelta(minutes=5))),
            )
            found = next(
                (
                    p
                    for p in listed
                    if _same_post(p, post.caption, due_iso) and p["status"] in ("scheduled", "sending", "sent")
                ),
                None,
            )
        if found is not None:
            remember(found["id"])
            return Placed(found["id"])
        if post.when is not None:
            if self._limit is None:
                self._limit = b.scheduled_limit()
            queued = b.queued(ch)
            if queued >= self._limit:
                raise BufferQueueFull(
                    f"Buffer queue full for {conf().publish.labels[ch]}: {queued} of "
                    f"{self._limit} scheduled posts (the plan's limit); delete one in Buffer, "
                    "then Retry"
                )
        created = b.create_post(
            ch,
            post_input(
                b.channels[ch],
                ch,
                post.caption,
                post.public_url,
                due_at=due_iso,
                youtube_title=post.title,
                first_comment=post.first_comment,
                thumbnail_offset_ms=post.cover_offset_ms,
            ),
        )
        remember(created["id"])
        if created.get("status") == "error":
            raise BufferError((created.get("error") or {}).get("message") or "Buffer says error")
        return Placed(created["id"])

    def status(self, channel: str, post_id: str) -> Status:
        post = self.client.get_post(post_id, channel)
        if post is None:
            return Status("gone", error="the post is gone from Buffer")
        if post["status"] == "sent":
            return Status("published", url=post.get("externalLink"), detail="sent")
        if post["status"] == "error":
            return Status("failed", error=(post.get("error") or {}).get("message") or "error", detail="error")
        return Status("pending", detail=post["status"])

    def delete(self, channel: str, post_id: str) -> None:
        post = self.client.get_post(post_id, channel)
        if post is None:
            return
        if post["status"] in ("sending", "sent"):
            raise BufferError(f"already {post['status']}")
        self.client.delete_post(post_id, channel)

    def exists(self, channel: str, post_id: str) -> bool:
        return self.client.get_post(post_id, channel) is not None


def _mutation_error(data: dict) -> str | None:
    """A mutation's error member ("InvalidInputError: ..."), for the call log; the
    caller raises on it."""
    for v in data.values():
        if isinstance(v, dict) and v.get("__typename", "").endswith("Error"):
            return f"{v['__typename']}: {v.get('message')}"
    return None


def _external_id(data: dict) -> str | None:
    for v in data.values():
        if isinstance(v, dict):
            if isinstance(v.get("post"), dict):
                return v["post"].get("id")
            if v.get("__typename") == "DeletePostSuccess":
                return v.get("id")
            if "id" in v and "status" in v:
                return v["id"]
    return None


def post_input(
    channel_id: str,
    channel: str,
    text: str,
    video_url: str,
    *,
    due_at: str | None,
    youtube_title: str | None = None,
    first_comment: str | None = None,
    thumbnail_offset_ms: int | None = None,
) -> dict:
    """createPost's input for one channel (PUBLISH settings): scheduled at `due_at`
    (ISO UTC) or now when None; never a draft, never awaiting approval. thumbnail_offset_ms:
    Instagram's and TikTok's cover frame (None: publish.thumbnail_offset_ms; a reel with a cold
    open passes it moved past the gag: reelline/pipeline/publish.py cover_offset_ms())."""
    video: dict = {"url": video_url}
    if channel in ("instagram", "tiktok"):
        offset = conf().publish.thumbnail_offset_ms if thumbnail_offset_ms is None else thumbnail_offset_ms
        video["metadata"] = {"thumbnailOffset": offset}
    ai = bool(conf().publish.ai_generated)
    if channel == "instagram":
        meta = {
            "instagram": {
                "type": "reel",
                "shouldShareToFeed": True,
                "isAiGenerated": ai,
                **({"firstComment": first_comment} if first_comment else {}),
            }
        }
    elif channel == "tiktok":
        meta = {"tiktok": {"isAiGenerated": ai}}
    elif channel == "youtube":
        meta = {"youtube": {"title": youtube_title, "isAiGenerated": ai, **conf().publish.youtube}}
    else:
        raise ValueError(f"Not a Buffer channel: {channel}")
    out = {
        "channelId": channel_id,
        "text": text,
        "schedulingType": "automatic",
        "mode": "customScheduled" if due_at else "shareNow",
        "saveToDraft": False,
        "needsApproval": False,
        "assets": [{"video": video}],
        "metadata": meta,
    }
    if due_at:
        out["dueAt"] = due_at
    return out
