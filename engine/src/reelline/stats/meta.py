"""Meta: Facebook Page reels (Graph: views, likes, comments, video insights, reach through the
reel's Page post) and Instagram insights (once the token has the scopes and IG is linked to the
Page). Reads go through reelline/pipeline/facebook.py's client (its token, its redaction)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.stats.base import (
    BaseCollector,
    Ctx,
    Followers,
    SourcePart,
    StatPub,
    empty,
    ig_shortcode,
)
from reelline.stats.base import (
    as_int as _int,
)

# A reel's video_insights, each checked one by one on v26.0 on 2026-10-03 (reels 4-6).
# post_impressions_unique (reach) is gone since Meta's 2026-06-15 deprecation; its
# replacement post_total_media_view_unique only exists on the reel's Page post
# (FB_POST_INSIGHTS, period lifetime: without it a day value of 0 comes too).
FB_INSIGHTS = (
    "fb_reels_total_plays",
    "post_video_avg_time_watched",
    "post_video_view_time",
    "post_video_social_actions",
    "post_video_followers",
)
FB_POST_INSIGHTS = ("post_total_media_view_unique",)
# A Page reel's own fields (Graph v26: no reactions edge on a listed reel; likes is it).
FB_FIELDS = "id,post_id,views,length,likes.summary(true).limit(0),comments.summary(true).limit(0)"
# reels_skip_rate (checked 2026-10-25): lifetime, a percent (60): the share of initial views
# that skipped in the first 3 seconds. It has no post_stats column; it stays in raw.
IG_INSIGHTS = (
    "views",
    "reach",
    "likes",
    "comments",
    "shares",
    "saved",
    "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time",
    "reels_skip_rate",
)


def parse_fb_reel(item: dict) -> dict:
    """A Graph video (views, reactions / likes and comments summaries) -> our fields."""

    def total(edge):
        return _int(((item.get(edge) or {}).get("summary") or {}).get("total_count"))

    out = empty()
    out.update(
        views=_int(item.get("views")),
        likes=total("reactions") if "reactions" in item else total("likes"),
        comments=total("comments"),
    )
    return out


def insight_values(data: list[dict]) -> dict:
    """Graph insights [{"name", "values": [{"value"}]} | {"name", "total_value": {"value"}}] -> name: value
    (a metric's lifetime entry wins over its other periods)."""
    out, lifetime = {}, set()
    for d in data or []:
        if d.get("name") in lifetime:
            continue
        if d.get("period") == "lifetime":
            lifetime.add(d.get("name"))
        if d.get("values"):
            out[d["name"]] = d["values"][-1].get("value")
        elif "total_value" in d:
            out[d["name"]] = (d["total_value"] or {}).get("value")
    return out


def parse_fb_insights(data: list[dict]) -> dict:
    v = insight_values(data)
    social = v.get("post_video_social_actions")
    out = empty()
    out.update(reach=_int(v.get("post_total_media_view_unique")), follows=_int(v.get("post_video_followers")))
    if isinstance(social, dict):  # Meta leaves out the zero ones: {"COMMENT": 1} is no share
        out["shares"] = _int(social.get("SHARE", social.get("share", 0)))
    if v.get("post_video_avg_time_watched") is not None:
        out["avg_watch_s"] = float(v["post_video_avg_time_watched"]) / 1000
    if v.get("post_video_view_time") is not None:
        out["total_watch_s"] = float(v["post_video_view_time"]) / 1000
    return out


def parse_ig_insights(data: list[dict]) -> dict:
    v = insight_values(data)
    out = empty()
    out.update(
        views=_int(v.get("views")),
        reach=_int(v.get("reach")),
        likes=_int(v.get("likes")),
        comments=_int(v.get("comments")),
        shares=_int(v.get("shares")),
        saves=_int(v.get("saved")),
    )
    if v.get("ig_reels_avg_watch_time") is not None:
        out["avg_watch_s"] = float(v["ig_reels_avg_watch_time"]) / 1000
    if v.get("ig_reels_video_view_total_time") is not None:
        out["total_watch_s"] = float(v["ig_reels_video_view_total_time"]) / 1000
    return out


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def meta_cache(conn: sqlite3.Connection) -> dict | None:
    raw = db.get_state(conn, "stats_meta")
    return json.loads(raw) if raw else None


def invalid_metric(exc: Exception) -> bool:
    """Meta's answer for a metric it doesn't (or no longer) have: (#100) ... valid insights metric."""
    return "valid insights metric" in str(exc)


def dropped_metrics(conn: sqlite3.Connection) -> set[str]:
    """The metrics dropped on this Graph version (a new version asks them all again)."""
    try:
        saved = json.loads(db.get_state(conn, "stats_meta_dropped") or "{}")
    except ValueError:
        return set()
    return set(saved.get("metrics") or []) if saved.get("graph_url") == conf().publish.graph_url else set()


def keep_dropped(conn: sqlite3.Connection, dropped: set[str], log: Callable[[str], None]) -> None:
    new = sorted(dropped - dropped_metrics(conn))
    if new:
        log(
            f"stats: Meta says {', '.join(new)} is no valid insights metric on {conf().publish.graph_url}: "
            "dropped, the other metrics still asked"
        )
        db.set_state(
            conn, "stats_meta_dropped", json.dumps({"graph_url": conf().publish.graph_url, "metrics": sorted(dropped)})
        )


def refresh_meta(conn: sqlite3.Connection, meta: MetaStats, now: datetime) -> dict | None:
    """The token's scopes and the linked IG account, cached a day (at once when the
    token changes)."""
    cache = meta_cache(conn)
    key = meta.token_key()
    if cache and cache.get("token") == key and now - datetime.fromisoformat(cache["at"]) < timedelta(days=1):
        return cache
    scopes = meta.scopes()
    ig = None
    if set(conf().steps.analytics.meta_ig_scopes) <= set(scopes):
        ig = meta.ig_user_id()
    cache = {"token": key, "at": db.now_iso(now), "scopes": scopes, "ig_user_id": ig}
    db.set_state(conn, "stats_meta", json.dumps(cache))
    return cache


class MetaStats:
    """The Graph reads, through reelline/pipeline/facebook.py's client (its token, its redaction)."""

    def __init__(self, fb):
        self.fb = fb
        self.dropped: set[str] = set()  # metrics Meta called invalid (collect loads and keeps them)

    def get(self, action: str, path: str, **params) -> dict:
        return self.fb._call(action, "GET", self.fb.graph(path), params=params)

    def insights(self, action: str, path: str, metrics: tuple[str, ...], **params) -> list[dict]:
        """The metrics not dropped, in one call. When Meta calls one invalid, each is asked
        by itself: the invalid ones go to self.dropped, the others' data comes back."""
        want = [m for m in metrics if m not in self.dropped]
        if not want:
            return []
        try:
            return self.get(action, path, metric=",".join(want), **params).get("data", [])
        except Exception as exc:
            if not invalid_metric(exc):
                raise
        data = []
        for m in want:
            try:
                data += self.get(action, path, metric=m, **params).get("data", [])
            except Exception as exc:
                if not invalid_metric(exc):
                    raise
                self.dropped.add(m)
        return data

    def token_key(self) -> str:
        return token_key(self.fb.settings.page_token)

    def scopes(self) -> list[str]:
        s = self.fb.settings
        d = self.get(
            "stats_fb_debug_token", "debug_token", input_token=s.page_token, access_token=f"{s.app_id}|{s.app_secret}"
        )["data"]
        return list(d.get("scopes") or [])

    def ig_user_id(self) -> str | None:
        page = self.get("stats_fb_page_ig", self.fb.page_id, fields="instagram_business_account")
        return (page.get("instagram_business_account") or {}).get("id")

    def page_followers(self) -> int | None:
        return _int(self.get("stats_fb_followers", self.fb.page_id, fields="followers_count").get("followers_count"))

    def reels(self) -> list[dict]:
        out, after = [], None
        while True:
            params = {"fields": FB_FIELDS, "limit": 100}
            if after:
                params["after"] = after
            body = self.get("stats_fb_reels", f"{self.fb.page_id}/video_reels", **params)
            out += body.get("data", [])
            after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
            if not (body.get("paging") or {}).get("next") or not after:
                return out

    def video(self, video_id: str) -> dict:
        return self.get("stats_fb_video", video_id, fields=FB_FIELDS)

    def video_insights(self, video_id: str) -> list[dict]:
        return self.insights("stats_fb_insights", f"{video_id}/video_insights", FB_INSIGHTS)

    def post_insights(self, post_id: str) -> list[dict]:
        """A reel's Page post (the video's post_id) insights: its reach."""
        path = post_id if "_" in str(post_id) else f"{self.fb.page_id}_{post_id}"
        return self.insights("stats_fb_post_insights", f"{path}/insights", FB_POST_INSIGHTS, period="lifetime")

    def ig_media(self, ig_user_id: str) -> list[dict]:
        return self.get(
            "stats_ig_media", f"{ig_user_id}/media", fields="id,shortcode,permalink,timestamp", limit=100
        ).get("data", [])

    def ig_insights(self, media_id: str) -> list[dict]:
        return self.get("stats_ig_insights", f"{media_id}/insights", metric=",".join(IG_INSIGHTS)).get("data", [])

    def ig_followers(self, ig_user_id: str) -> int | None:
        return _int(self.get("stats_ig_followers", ig_user_id, fields="followers_count").get("followers_count"))


class MetaCollector(BaseCollector):
    name = "meta"
    sources: tuple[str, ...] = ("facebook", "facebook_insights", "instagram_graph")
    channels = {"facebook": ("facebook",), "facebook_insights": ("facebook",), "instagram_graph": ("instagram",)}
    depends = {"facebook_insights": "facebook"}

    def __init__(self, meta: MetaStats | None):
        self.meta = meta
        self._post_ids: dict[int, str] = {}

    def missing(self) -> dict[str, str]:
        if self.meta is not None:
            return {}
        need = "META_* keys in .env"
        return {"facebook": need, "facebook_insights": need, "instagram_graph": need}

    def prepare(self, ctx: Ctx):
        if self.meta is None:
            return None
        try:
            info = refresh_meta(ctx.conn, self.meta, ctx.now)
        except Exception as exc:
            ctx.log(f"stats: Meta scopes unknown ({type(exc).__name__}: {exc}); using the last known")
            info = meta_cache(ctx.conn)
        self.meta.dropped = dropped_metrics(ctx.conn)
        return info

    def collect(self, source: str, pubs: list[StatPub], ctx: Ctx):
        meta = self.meta
        if source == "facebook":
            self._post_ids = {}
            listed = {str(x["id"]): x for x in meta.reels()}
            for p in pubs:
                ctx.set_native(p, str(p.external_id))
                item = listed.get(str(p.external_id)) or meta.video(p.external_id)
                fields = parse_fb_reel(item)
                if fields["comments"] is not None:  # the Page's own source comment isn't engagement
                    fields["comments"] = max(0, fields["comments"] - p.own_comments)
                if item.get("post_id"):
                    self._post_ids[p.id] = str(item["post_id"])
                yield p, SourcePart(fields, {"facebook": {k: item.get(k) for k in ("views", "length")}})
        elif source == "facebook_insights":
            for p in pubs:
                data = meta.video_insights(p.external_id)
                if p.id in self._post_ids:
                    data += meta.post_insights(self._post_ids[p.id])
                yield p, SourcePart(parse_fb_insights(data), {"facebook_insights": insight_values(data)})
        else:
            ig_user = (ctx.info.get(self.name) or {}).get("ig_user_id")
            if any(not p.native_id for p in pubs):
                media = {
                    m.get("shortcode") or ig_shortcode(m.get("permalink")): m["id"] for m in meta.ig_media(ig_user)
                }
                for p in pubs:
                    if not p.native_id and media.get(ig_shortcode(p.url)):
                        ctx.set_native(p, media[ig_shortcode(p.url)])
            for p in pubs:
                if p.native_id:
                    data = meta.ig_insights(p.native_id)
                    yield p, SourcePart(parse_ig_insights(data), {"instagram_graph": insight_values(data)})

    def finish(self, source: str, ctx: Ctx) -> None:
        if source == "facebook_insights":
            keep_dropped(ctx.conn, self.meta.dropped, ctx.log)

    def follower_sources(self, ctx: Ctx) -> tuple[str, ...]:
        return ("facebook", "instagram_graph")

    def followers(self, source: str, ctx: Ctx) -> Followers | None:
        if source == "facebook":
            return Followers("facebook", self.meta.page_followers(), "graph")
        info = meta_cache(ctx.conn) or {}
        return Followers("instagram", self.meta.ig_followers(info["ig_user_id"]), "ig_graph")
