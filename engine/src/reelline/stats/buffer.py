"""Buffer's post metrics (Instagram, TikTok, YouTube in one call), spent from a rate budget that
publishing shares (the last RateLimit seen is kept in bot_state; stats leave Buffer alone when a
window is under its margin). Client: reelline/pipeline/buffer.py BufferClient.post_metrics()."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from reelline.config import conf
from reelline.pipeline import db
from reelline.stats.base import BaseCollector, Ctx, SourcePart, StatPub, as_int, empty, parse_ts, utcnow

BUFFER_CHANNELS = ("instagram", "tiktok", "youtube")


def limits_sink(conn: sqlite3.Connection, now: Callable[[], datetime] = utcnow) -> Callable[[dict], None]:
    """BufferClient's on_limits: the last RateLimit seen, kept in bot_state."""

    def sink(windows: dict) -> None:
        db.set_state(conn, "buffer_ratelimit", json.dumps({"seen_at": db.now_iso(now()), "windows": windows}))

    return sink


def buffer_limits(conn: sqlite3.Connection) -> dict | None:
    raw = db.get_state(conn, "buffer_ratelimit")
    return json.loads(raw) if raw else None


def buffer_blocked(limits: dict | None, now: datetime, margins: dict | None = None) -> str | None:
    """Why stats must leave Buffer alone now (a window under its margin and not reset
    since it was seen), None when they may call it."""
    if not limits:
        return None
    margins = margins or conf().steps.analytics.buffer_margins
    seen = datetime.fromisoformat(limits["seen_at"])
    names = {"15min": "in 15 min", "1day": "today", "30days": "in 30 days"}
    for window, margin in margins.items():
        w = limits["windows"].get(window)
        if w is None or now >= seen + timedelta(seconds=w["reset_s"]):
            continue
        if w["remaining"] < margin:
            return (
                f"Buffer budget low: {w['remaining']} of {w['limit']} calls left {names.get(window, window)} "
                f"(margin {margin}, kept for publishing)"
            )
    return None


def parse_buffer(post: dict) -> dict:
    """A Buffer post node with metrics -> our fields (None where it gives nothing).
    TikTok's reach is always 0 through Buffer: None."""
    m = {x["type"]: x["value"] for x in post.get("metrics") or []}
    out = empty()
    out.update(
        views=as_int(m.get("views")),
        likes=as_int(m.get("reactions")),
        comments=as_int(m.get("comments")),
        shares=as_int(m.get("shares")),
        saves=as_int(m.get("saves")),
        reach=as_int(m.get("reach")),
        follows=as_int(m.get("follows")),
    )
    if m.get("averageTimeWatched") is not None:
        out["avg_watch_s"] = float(m["averageTimeWatched"])
    if m.get("totalTimeWatched") is not None:
        out["total_watch_s"] = float(m["totalTimeWatched"]) * 60  # minutes
    if post.get("channelService") == "tiktok" and not out["reach"]:
        out["reach"] = None
    return out


class BufferStats(BaseCollector):
    name = "buffer"
    sources: tuple[str, ...] = ("buffer",)
    channels = {"buffer": BUFFER_CHANNELS}

    def __init__(self, client: Any):
        self.client = client

    def missing(self) -> dict[str, str]:
        return {} if self.client is not None else {"buffer": "BUFFER_* keys in .env"}

    def hold(self, source: str, ctx: Ctx) -> str | None:
        return buffer_blocked(buffer_limits(ctx.conn), ctx.now)

    def collect(self, source: str, pubs: list[StatPub], ctx: Ctx):
        start = min(p.published_at for p in pubs) - timedelta(days=3)
        posts = {
            x["id"]: x
            for x in self.client.post_metrics(
                sorted({p.channel for p in pubs}), (db.now_iso(start), db.now_iso(ctx.now))
            )
        }
        for p in pubs:
            post = posts.get(p.external_id)
            if post is None:
                continue
            yield (
                p,
                SourcePart(
                    parse_buffer(post),
                    {"buffer": {x["type"]: x["value"] for x in post.get("metrics") or []}},
                    parse_ts(post.get("metricsUpdatedAt")),
                ),
            )
