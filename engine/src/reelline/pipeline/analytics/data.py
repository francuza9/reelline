"""The data: what the report reads from the database (the only database read is load())."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.pipeline import db
from reelline.pipeline.stats import METRICS, buffer_blocked, capabilities, meta_cache

PLATFORMS = conf().steps.analytics.platforms


class AnalyticsError(ReellineError, RuntimeError):
    """A bad period or reel."""


@dataclass
class Stat:
    as_of: datetime
    fetched_at: datetime
    age_h: float
    target_h: float | None
    values: dict
    pct_derived: bool
    source: str


@dataclass
class Pub:
    id: int
    reel_id: int
    channel: str
    url: str | None
    published_at: datetime
    stats: list[Stat] = field(default_factory=list)  # by as_of


@dataclass
class Reel:
    id: int
    title: str
    topic: str | None
    posted_at: datetime
    duration_s: float | None
    pubs: list[Pub] = field(default_factory=list)
    opening_version: int | None = None  # reels.opening_version
    gag: str | None = None  # reels.gag: the cold open (reelline/media/gags.py)
    gag_mode: str | None = None  # reels.gag_mode: ab for the A/B's reels


@dataclass
class Data:
    now: datetime
    reels: dict[int, Reel]
    followers: dict[str, list[tuple[str, int]]]  # platform -> [(Tbilisi day, n)] by day
    spend: list[tuple[datetime, float]]  # every cost row (llm_calls)
    caps: dict[str, str | None]  # reelline/pipeline/stats.py capabilities()
    failing: dict[str, tuple[int, str, str]]  # source -> (failures, error, since)
    buffer_limits: dict | None = None
    buffer_block: str | None = None


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def load(conn: sqlite3.Connection, now: datetime, youtube_settings=None) -> Data:
    """Everything the reports read, in one go."""
    if youtube_settings is None:
        from reelline.config.credentials import ConfigError, load_youtube

        try:
            youtube_settings = load_youtube()
        except ConfigError:
            youtube_settings = None
    pubs: dict[int, Pub] = {}
    for r in conn.execute("""SELECT id, reel_id, channel, url, published_at FROM publications
                             WHERE state = 'published' AND published_at IS NOT NULL ORDER BY id""").fetchall():
        pubs[r["id"]] = Pub(r["id"], r["reel_id"], r["channel"], r["url"], _dt(r["published_at"]))
    for r in conn.execute("SELECT * FROM post_stats ORDER BY as_of, fetched_at, id").fetchall():
        if r["publication_id"] in pubs:
            pubs[r["publication_id"]].stats.append(
                Stat(
                    _dt(r["as_of"]),
                    _dt(r["fetched_at"]),
                    r["age_h"],
                    r["target_h"],
                    {k: r[k] for k in METRICS},
                    bool(r["pct_derived"]),
                    r["source"],
                )
            )
    reels: dict[int, Reel] = {}
    ids = sorted({p.reel_id for p in pubs.values()})
    for rid in ids:
        r = conn.execute(
            """SELECT r.id, r.duration_s, r.opening_version, r.gag, r.gag_mode, s.title,
                                   (SELECT c.youtube_title FROM captions c WHERE c.script_id = r.script_id
                                    ORDER BY c.id DESC LIMIT 1) AS yt_title,
                                   (SELECT a.topic FROM assessments a WHERE a.story_id = p.story_id) AS topic
                            FROM reels r JOIN picks p ON p.id = r.pick_id JOIN stories s ON s.id = p.story_id
                            WHERE r.id = ?""",
            (rid,),
        ).fetchone()
        mine = [p for p in pubs.values() if p.reel_id == rid]
        reels[rid] = Reel(
            rid,
            (r["yt_title"] or r["title"] or f"reel #{rid}") if r else f"reel #{rid}",
            r["topic"] if r else None,
            min(p.published_at for p in mine),
            r["duration_s"] if r else None,
            sorted(mine, key=lambda p: PLATFORMS.index(p.channel)),
            r["opening_version"] if r else None,
            r["gag"] if r else None,
            r["gag_mode"] if r else None,
        )
    followers: dict[str, list] = {}
    for r in conn.execute("SELECT platform, day, followers FROM account_stats ORDER BY day").fetchall():
        followers.setdefault(r["platform"], []).append((r["day"], r["followers"]))
    spend = [
        (_dt(r[0]), r[1])
        for r in conn.execute("SELECT called_at, cost_usd FROM llm_calls WHERE cost_usd IS NOT NULL").fetchall()
    ]
    failing = {
        r["source"]: (r["failures"], r["error"], r["failing_since"])
        for r in conn.execute("SELECT * FROM stats_health WHERE failures > 0").fetchall()
    }
    raw = db.get_state(conn, "buffer_ratelimit")
    limits = json.loads(raw) if raw else None
    return Data(
        now,
        reels,
        followers,
        spend,
        capabilities(meta_cache(conn), youtube_settings),
        failing,
        limits,
        buffer_blocked(limits, now),
    )
