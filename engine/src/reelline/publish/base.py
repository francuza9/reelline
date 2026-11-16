"""Publisher: put one platform's render on that platform and follow it.

A channel (instagram, tiktok, youtube, facebook, ...) is a name the pipeline stores publications
under; `publish.targets` says which adapter publishes each one. The pipeline (reelline/pipeline/
publish.py) decides *what* and *when*: the guard, the files in blob storage, the slot, the DB rows,
the retries. A Publisher does the platform's part:

    publish(post, existing=, remember=)   create the post (scheduled for post.when, now when None);
                                          `existing` is the id stored from an earlier try: the
                                          adapter looks at it first, so a retry never double posts;
                                          `remember(id)` stores an id as soon as the platform has one
    status(channel, id)                   where the post is now (Status.state: pending | published |
                                          failed | gone)
    delete(channel, id)                   take it back (before it is live)

Adapters raise PublisherError (QueueFull: the platform's schedule limit) with a message that says
why and never holds a secret. `media` says which URL of the file the platform fetches: "public"
(blob storage's plain URL), "signed" (an expiring one) or "file" (it reads post.path itself).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from reelline.core.errors import PublishError


class PublisherError(PublishError):
    """A platform call that failed or was refused (the message says why)."""


class QueueFull(PublisherError):
    """The platform's schedule is at its limit."""


@dataclass(frozen=True)
class Post:
    channel: str
    caption: str
    path: Path  # the channel's render
    public_url: str | None = None  # blob storage's plain URL of it
    signed_url: str | None = None  # an expiring one
    title: str | None = None  # YouTube's title
    first_comment: str | None = None
    cover: Path | None = None
    cover_offset_ms: int | None = None  # the cover frame, into the video
    when: datetime | None = None  # aware; None = now
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Placed:
    id: str
    state: Literal["scheduled", "published"] = "scheduled"
    url: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Status:
    state: Literal["pending", "published", "failed", "gone"]
    url: str | None = None
    error: str | None = None
    detail: str | None = None  # the platform's own word for the state


MediaKind = Literal["public", "signed", "file"]  # what the platform fetches: a plain URL, a signed URL, the file


@runtime_checkable
class Publisher(Protocol):
    name: str

    @property
    def channels(self) -> tuple[str, ...]: ...

    @property
    def media(self) -> MediaKind: ...

    schedules: bool

    def publish(
        self,
        post: Post,
        *,
        existing: str | None = None,
        now: datetime | None = None,
        remember: Callable[[str], None] = lambda id: None,
    ) -> Placed: ...
    def status(self, channel: str, post_id: str) -> Status: ...
    def delete(self, channel: str, post_id: str) -> None: ...
    def exists(self, channel: str, post_id: str) -> bool: ...
