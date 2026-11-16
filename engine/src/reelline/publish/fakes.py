"""FakePublisher: a Publisher that records its posts (no network)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from reelline.publish.base import MediaKind, Placed, Post, PublisherError, Status


class FakePublisher:
    name = "fake"
    media: MediaKind = "public"
    schedules = True

    def __init__(self, channels: tuple[str, ...] = ("instagram",), fail: dict[str, str] | None = None):
        self.channels = tuple(channels)
        self.fail = fail or {}  # channel -> error message
        self.posts: dict[str, Post] = {}
        self.state: dict[str, str] = {}
        self.deleted: list[str] = []

    def publish(
        self,
        post: Post,
        *,
        existing: str | None = None,
        now: datetime | None = None,
        remember: Callable[[str], None] = lambda id: None,
    ) -> Placed:
        if post.channel in self.fail:
            raise PublisherError(self.fail[post.channel])
        if existing and existing in self.posts:
            return Placed(existing)
        pid = f"fake{len(self.posts) + 1}"
        self.posts[pid], self.state[pid] = post, "scheduled"
        remember(pid)
        if post.when is None:
            self.state[pid] = "published"
        return Placed(pid, "published" if post.when is None else "scheduled")

    def status(self, channel: str, post_id: str) -> Status:
        if post_id not in self.posts:
            return Status("gone", error="the post is gone")
        s = self.state[post_id]
        return Status(
            "published" if s == "published" else "pending",
            url=f"https://fake.test/{post_id}" if s == "published" else None,
            detail=s,
        )

    def delete(self, channel: str, post_id: str) -> None:
        self.posts.pop(post_id, None)
        self.deleted.append(post_id)

    def exists(self, channel: str, post_id: str) -> bool:
        return post_id in self.posts
