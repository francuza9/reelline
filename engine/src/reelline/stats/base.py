"""The stats interface: what the sweep core (reelline/pipeline/stats.py) asks of a platform.

A StatsCollector reads one platform's numbers for published posts: views, likes, watch time, the
retention curve, skip rate (kept in `raw`), followers. It owns one or more *sources* (a source is
one API surface: "youtube_data", "youtube_analytics"); the core owns the schedule (which post is
due which snapshot), the merge of sources into one post_stats row, per-source health and alerts.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.pipeline import db

METRICS = (
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "reach",
    "follows",
    "avg_watch_s",
    "total_watch_s",
    "avg_pct",
)


class StatsError(ReellineError, RuntimeError):
    """A stats API call that failed (the platform's message, no secrets)."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def empty() -> dict[str, Any]:
    return dict.fromkeys(METRICS)


def as_int(x: Any) -> int | None:
    return None if x is None else round(float(x))


def parse_ts(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", text):  # Graph's +0000
        text = text[:-2] + ":" + text[-2:]
    return datetime.fromisoformat(text).astimezone(UTC)


def native_from_url(channel: str, url: str | None) -> str | None:
    """The platform's own id from a post URL (YouTube, TikTok); None otherwise."""
    if not url:
        return None
    pats = {"youtube": r"(?:[?&]v=|/shorts/|youtu\.be/)([\w-]{11})", "tiktok": r"/video/(\d+)"}
    m = re.search(pats[channel], url) if channel in pats else None
    return m.group(1) if m else None


def ig_shortcode(url: str | None) -> str | None:
    m = re.search(r"instagram\.com/(?:reel|reels|p)/([\w-]+)", url or "")
    return m.group(1) if m else None


def targets() -> tuple[float, ...]:
    """Every snapshot age (hours): the fixed ones, then weekly."""
    a = conf().steps.analytics
    weekly = tuple(24 * d for d in range(a.weekly_after_days + 7, a.weekly_until_days + 1, 7))
    return tuple(sorted(set(a.snapshots_h) | set(weekly)))


def due_target(
    published_at: datetime, now: datetime, done: set[float] | frozenset[float] = frozenset()
) -> float | None:
    """The snapshot point due now: the latest one reached that's later than every one
    already taken (missed earlier points are not caught up). None: nothing due."""
    age = (now - published_at).total_seconds() / 3600
    reached = [t for t in targets() if t <= age]
    if not reached:
        return None
    t = reached[-1]
    return None if any(d >= t for d in done) else t


@dataclass
class StatPub:
    id: int
    reel_id: int
    channel: str
    external_id: str | None
    url: str | None
    native_id: str | None
    published_at: datetime
    duration_s: float | None
    done: set[str] = field(default_factory=set)
    own_comments: int = 0  # our own comments under it (the source line on Facebook)


@dataclass
class SourcePart:
    """One source's answer for one post: `fields` (METRICS keys, None where it gives nothing; None
    for no fields, a raw-only source), `raw` (what post_stats.raw keeps, by key), `as_of` (when the
    numbers are from, when the source knows it)."""

    fields: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    as_of: datetime | None = None


@dataclass(frozen=True)
class Followers:
    platform: str
    count: int | None
    source: str  # account_stats.source


@dataclass
class Ctx:
    """What a round hands its collectors."""

    conn: sqlite3.Connection
    now: datetime
    log: Callable[[str], None] = print
    info: dict[str, Any] = field(default_factory=dict)  # collector name -> what its prepare() returned

    def set_native(self, pub: StatPub, native_id: str) -> None:
        if pub.native_id != native_id:
            pub.native_id = native_id
            with db.transaction(self.conn):
                self.conn.execute("UPDATE publications SET native_id = ? WHERE id = ?", (native_id, pub.id))


@runtime_checkable
class StatsCollector(Protocol):
    name: str
    sources: tuple[str, ...]  # in fetch order
    channels: dict[str, tuple[str, ...]]  # source -> the channels' posts it reads
    silent: tuple[str, ...]  # raw-only sources: no health row, they handle their own errors
    depends: dict[str, str]  # source -> the source that must be on for it

    def prepare(self, ctx: Ctx) -> Any:
        """Once a round, first: e.g. a token's scopes. The result is ctx.info[name]."""

    def missing(self) -> dict[str, str]:
        """source -> what it needs, for the sources this collector can't run at all (no client)."""

    def hold(self, source: str, ctx: Ctx) -> str | None:
        """Why the source must be left alone now (a rate budget), None to go ahead."""

    def eligible(self, source: str, pub: StatPub, ctx: Ctx) -> bool:
        """Whether `pub` is read by the source (the core asks only for the source's channels)."""

    def collect(self, source: str, pubs: list[StatPub], ctx: Ctx) -> Iterable[tuple[StatPub, SourcePart]]:
        """The parts, one per post it has numbers for. May be a generator: what came before an
        exception is kept."""

    def finish(self, source: str, ctx: Ctx) -> None:
        """After a source ran (ok or not)."""

    def follower_sources(self, ctx: Ctx) -> tuple[str, ...]:
        """The sources that can give followers now."""

    def followers(self, source: str, ctx: Ctx) -> Followers | None: ...


class BaseCollector:
    """Defaults, so an adapter says only what it does."""

    name = ""
    sources: tuple[str, ...] = ()
    channels: dict[str, tuple[str, ...]] = {}
    silent: tuple[str, ...] = ()
    depends: dict[str, str] = {}

    def prepare(self, ctx: Ctx) -> Any:
        return None

    def missing(self) -> dict[str, str]:
        return {}

    def hold(self, source: str, ctx: Ctx) -> str | None:
        return None

    def eligible(self, source: str, pub: StatPub, ctx: Ctx) -> bool:
        return True

    def collect(self, source: str, pubs: list[StatPub], ctx: Ctx) -> Iterable[tuple[StatPub, SourcePart]]:
        return ()

    def finish(self, source: str, ctx: Ctx) -> None:
        pass

    def follower_sources(self, ctx: Ctx) -> tuple[str, ...]:
        return ()

    def followers(self, source: str, ctx: Ctx) -> Followers | None:
        return None
