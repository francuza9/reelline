"""YouTube: the Data API (views, likes, comments, channel subscribers; an API key) and the
Analytics API (shares, subscribers gained, average view duration / percentage, the retention
curve; the channel owner's OAuth token). Client: reelline/pipeline/youtube.py YouTubeClient."""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

from reelline.pipeline import db, retention
from reelline.stats.base import BaseCollector, Ctx, Followers, SourcePart, StatPub, as_int, due_target, empty


def parse_yt_video(item: dict) -> dict:
    s = item.get("statistics") or {}
    out = empty()
    out.update(
        views=as_int(s.get("viewCount")), likes=as_int(s.get("likeCount")), comments=as_int(s.get("commentCount"))
    )
    return out


def parse_yt_analytics(row: dict) -> dict:
    """An Analytics row; its views lag a day or two, so they're left out (Data API or
    Buffer give them)."""
    out = empty()
    out.update(shares=as_int(row.get("shares")), follows=as_int(row.get("subscribersGained")))
    if row.get("averageViewDuration") is not None:
        out["avg_watch_s"] = float(row["averageViewDuration"])
    if row.get("averageViewPercentage") is not None:
        out["avg_pct"] = float(row["averageViewPercentage"])
    return out


class YouTubeStats(BaseCollector):
    name = "youtube"
    sources: tuple[str, ...] = ("youtube_data", "youtube_analytics", "youtube_retention")
    channels = {s: ("youtube",) for s in sources}
    silent: tuple[str, ...] = ("youtube_retention",)  # a failed curve never fails the source
    depends = {"youtube_retention": "youtube_analytics"}

    def __init__(self, client: Any, settings: Any = None):
        self.client, self.settings = client, settings

    def prepare(self, ctx: Ctx):
        return self.settings

    def eligible(self, source: str, pub: StatPub, ctx: Ctx) -> bool:
        if not pub.native_id:
            return False
        if source == "youtube_retention":
            return retention.wanted(due_target(pub.published_at, ctx.now, pub.done))
        return True

    def collect(self, source: str, pubs: list[StatPub], ctx: Ctx):
        if source == "youtube_data":
            items = {x["id"]: x for x in self.client.videos([p.native_id for p in pubs])}
            for p in pubs:
                if p.native_id in items:
                    item = items[p.native_id]
                    yield p, SourcePart(parse_yt_video(item), {"youtube_data": item.get("statistics")})
                    channel_id = (item.get("snippet") or {}).get("channelId")
                    if channel_id and db.get_state(ctx.conn, "youtube_channel_id") != channel_id:
                        db.set_state(ctx.conn, "youtube_channel_id", channel_id)
        elif source == "youtube_analytics":
            start = min(p.published_at for p in pubs).astimezone(UTC).date() - timedelta(days=1)
            rows = {r["video"]: r for r in self.client.video_report([p.native_id for p in pubs], start, ctx.now.date())}
            for p in pubs:
                if p.native_id in rows:
                    yield p, SourcePart(parse_yt_analytics(rows[p.native_id]), {"youtube_analytics": rows[p.native_id]})
        else:
            for p in pubs:  # one video per call
                try:
                    curve = self.client.retention(
                        p.native_id, p.published_at.astimezone(UTC).date() - timedelta(days=1), ctx.now.date()
                    )
                except Exception as exc:
                    ctx.log(f"stats: retention curve of {p.native_id} failed: {type(exc).__name__}: {exc}"[:400])
                    continue
                if curve:
                    yield p, SourcePart(None, {"youtube_retention": retention.curve_raw(curve, p.duration_s)})

    def follower_sources(self, ctx: Ctx) -> tuple[str, ...]:
        return ("youtube_data",) if db.get_state(ctx.conn, "youtube_channel_id") else ()

    def followers(self, source: str, ctx: Ctx) -> Followers | None:
        ch = self.client.channel(db.get_state(ctx.conn, "youtube_channel_id"))
        s = (ch or {}).get("statistics") or {}
        if s.get("hiddenSubscriberCount"):
            return None
        return Followers("youtube", as_int(s.get("subscriberCount")), "yt_data")
