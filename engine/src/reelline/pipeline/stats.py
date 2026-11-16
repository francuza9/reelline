"""Performance stats of the published reels: read-only snapshots from each platform
into post_stats, followers into account_stats (reelline/pipeline/db.py migration 15). Nothing
is ever posted or changed on a platform. steps.analytics in the channel config has the
schedule, the margins and the labels; reelline/pipeline/analytics.py makes the reports.

Usage:
  python -m reelline.pipeline.stats fetch      the due snapshots now (what the tick does)
  python -m reelline.pipeline.stats sweep      every published reel + followers now (the daily sweep)
  python -m reelline.pipeline.stats backfill   the same, for reels published before stats existed
  python -m reelline.pipeline.stats status     sources on/off and why, health, Buffer's budget, calls per day

Sources, merged per publication into one row per fetch:
  buffer             Instagram, TikTok, YouTube: Buffer's Post.metrics (views, reactions,
                     comments, shares, saves, reach, follows; TikTok total and average
                     watch time). One call for every post. Buffer refreshes them about
                     daily, so as_of is its metricsUpdatedAt, not the fetch.
  facebook           the Page's reels (views, reactions, comments) and the Page's
                     followers_count, with the publishing token (pages_read_engagement).
  facebook_insights  video_insights (average and total watch time, shares, follows) and
                     the reel's Page post insights (reach): on once the token has
                     read_insights. A metric Meta answers "must be a valid insights
                     metric" for is dropped (logged once, kept in bot_state per Graph
                     version) and the rest asked again: one never fails the source.
  instagram_graph    media insights (views, reach, average and total watch time, ...)
                     and followers: on once the token has instagram_basic +
                     instagram_manage_insights and the IG account is linked to the Page.
  youtube_data       videos.list statistics and the channel's subscribers: on with
                     YOUTUBE_API_KEY.
  youtube_analytics  average view duration and %, shares, subscribers gained: on with
                     YOUTUBE_CLIENT_ID / _SECRET / _REFRESH_TOKEN (reelline/pipeline/stats_auth.py).
The Meta scopes are read with debug_token once a day, and at once when the token in
.env changes, so a re-authorized token switches its sources on by itself.

When: snapshots at steps.analytics.snapshots_h after a publication went live, then weekly;
the tick (reelline/pipeline/run_daily.py) takes whatever is due, batched into one call per API;
a point missed while the server was down is not caught up (the latest due one is
taken). Once a day at steps.analytics.sweep_at every published reel and the followers.
A publication whose sources all failed gets no row (its point stays due).

Buffer's budget is shared with publishing: Buffer is skipped while the last seen
RateLimit remainder of any window is under steps.analytics.buffer_margins (every Buffer
reply updates it, publishing's too). One source failing never stops the others; a
source failing steps.analytics.alert_after_failures rounds in a row alerts once in
Telegram, and once when it's back. Every call is in publish_calls (action stats_*),
endpoint only: no key, no token.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.scoring import tbilisi

# Each platform's reads, parsers and budget live in reelline/stats; the names stay importable from here.
from reelline.stats.base import (
    METRICS,
    Ctx,
    Followers,
    due_target,
    empty,
    ig_shortcode,
    native_from_url,
    parse_ts,
    targets,
    utcnow,
)
from reelline.stats.base import (
    StatPub as Pub,
)
from reelline.stats.base import (
    as_int as _int,
)
from reelline.stats.buffer import (
    BUFFER_CHANNELS,
    buffer_blocked,
    buffer_limits,
    limits_sink,
    parse_buffer,
)
from reelline.stats.meta import (
    FB_FIELDS,
    FB_INSIGHTS,
    FB_POST_INSIGHTS,
    IG_INSIGHTS,
    MetaStats,
    dropped_metrics,
    insight_values,
    invalid_metric,
    keep_dropped,
    meta_cache,
    parse_fb_insights,
    parse_fb_reel,
    parse_ig_insights,
    refresh_meta,
    token_key,
)
from reelline.stats.registry import get_collectors
from reelline.stats.youtube import parse_yt_analytics, parse_yt_video

# Re-exported: tests, comments.py and the dev scripts reach these as stats.<name>.
__all__ = [
    "BUFFER_CHANNELS",
    "FB_FIELDS",
    "FB_INSIGHTS",
    "FB_POST_INSIGHTS",
    "IG_INSIGHTS",
    "_int",
    "dropped_metrics",
    "ig_shortcode",
    "insight_values",
    "invalid_metric",
    "keep_dropped",
    "parse_buffer",
    "parse_fb_insights",
    "parse_fb_reel",
    "parse_ig_insights",
    "parse_ts",
    "parse_yt_analytics",
    "parse_yt_video",
    "refresh_meta",
    "targets",
    "token_key",
]

SOURCES = ("buffer", "facebook", "facebook_insights", "instagram_graph", "youtube_data", "youtube_analytics")
# Which sources feed which channel, lowest priority first (a later non-null value wins).
CHANNEL_SOURCES = {
    "instagram": ("buffer", "instagram_graph"),
    "tiktok": ("buffer", "tiktok_business"),
    "youtube": ("buffer", "youtube_analytics", "youtube_data"),
    "facebook": ("facebook", "facebook_insights"),
}
# Sources that read the platform directly (their numbers are as of the fetch).
DIRECT = ("facebook", "instagram_graph", "youtube_data", "tiktok_business")
# ---- Parsing: each API's payload -> our fields (pure) ------------------------------------------------


def merge(channel: str, parts: dict[str, dict], duration_s: float | None) -> tuple[dict, bool]:
    """One channel's fields from its sources (lowest priority first, a later non-null
    value wins); % viewed derived from the average watch time and the reel's length
    when no source gives it. Returns (fields, pct_derived)."""
    out = empty()
    for src in CHANNEL_SOURCES[channel]:
        for k, v in (parts.get(src) or {}).items():
            if v is not None and k in out:
                out[k] = v
    derived = False
    if out["avg_pct"] is None and out["avg_watch_s"] is not None and duration_s:
        out["avg_pct"] = round(out["avg_watch_s"] / duration_s * 100, 1)
        derived = True
    return out, derived


# ---- Health ----------------------------------------------------------------------------------


def health(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["source"]: r for r in conn.execute("SELECT * FROM stats_health").fetchall()}


def record_ok(conn: sqlite3.Connection, source: str, now: datetime, say: Callable[[str], None]) -> None:
    prev = health(conn).get(source)
    if prev is not None and prev["alerted"]:
        say(f"✅ Stats: {label(source)} works again (failed since {local_hm(prev['failing_since'])}).")
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO stats_health (source, ok_at, failing_since, failures, error, alerted, updated_at)
                        VALUES (?, ?, NULL, 0, NULL, 0, ?)
                        ON CONFLICT (source) DO UPDATE SET ok_at = excluded.ok_at, failing_since = NULL, failures = 0,
                            error = NULL, alerted = 0, updated_at = excluded.updated_at""",
            (source, db.now_iso(now), db.now_iso(now)),
        )


def record_fail(conn: sqlite3.Connection, source: str, error: str, now: datetime, say: Callable[[str], None]) -> None:
    prev = health(conn).get(source)
    failures = (prev["failures"] if prev else 0) + 1
    since = (prev["failing_since"] if prev and prev["failing_since"] else None) or db.now_iso(now)
    alerted = bool(prev and prev["alerted"])
    if not alerted and failures >= conf().steps.analytics.alert_after_failures:
        say(
            f"⚠️ Stats: {label(source)} failing since {local_hm(since)} ({failures} rounds): {error}"[:600]
            + "\nThe other platforms go on; /analytics shows it as n/a."
        )
        alerted = True
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO stats_health (source, ok_at, failing_since, failures, error, alerted, updated_at)
                        VALUES (?, NULL, ?, ?, ?, ?, ?)
                        ON CONFLICT (source) DO UPDATE SET failing_since = excluded.failing_since,
                            failures = excluded.failures, error = excluded.error, alerted = excluded.alerted,
                            updated_at = excluded.updated_at""",
            (source, since, failures, error[:500], int(alerted), db.now_iso(now)),
        )


SOURCE_LABELS = {
    "buffer": "Buffer (IG, TikTok, YouTube)",
    "facebook": "Facebook",
    "facebook_insights": "Facebook insights",
    "instagram_graph": "Instagram insights",
    "youtube_data": "YouTube Data API",
    "youtube_analytics": "YouTube Analytics",
}


def label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def local_hm(iso: str | None) -> str:
    if not iso:
        return "-"
    at = datetime.fromisoformat(iso).astimezone(tbilisi())
    return at.strftime("%H:%M") if at.date() == utcnow().astimezone(tbilisi()).date() else at.strftime("%b %d %H:%M")


# ---- Capabilities: which sources are on, and what the others need ---------------------------------


def capabilities(meta: dict | None, youtube) -> dict[str, str | None]:
    """source -> None (on) or what it needs. `meta`: the cached debug_token result;
    `youtube`: YouTubeSettings, or None / False for none."""
    scopes = set((meta or {}).get("scopes") or [])
    needs = conf().steps.analytics.needs
    return {
        "buffer": None,
        "facebook": None,
        "facebook_insights": None
        if conf().steps.analytics.meta_insights_scope in scopes
        else needs["facebook_insights"],
        "instagram_graph": None
        if set(conf().steps.analytics.meta_ig_scopes) <= scopes and (meta or {}).get("ig_user_id")
        else needs["instagram_graph"],
        "youtube_data": None if getattr(youtube, "api_key", None) else needs["youtube_data"],
        "youtube_analytics": None if getattr(youtube, "analytics", False) else needs["youtube_analytics"],
    }


@dataclass
class Apis:
    buffer: object | None  # reelline/pipeline/buffer.py BufferClient (post_metrics)
    meta: object | None  # MetaStats
    youtube: object | None  # reelline/pipeline/youtube.py YouTubeClient
    youtube_settings: object | None = None
    tiktok: object | None = None  # reelline/stats/tiktok.py TikTokClient (steps.analytics.collectors names "tiktok")
    extra: list = field(default_factory=list)  # collectors added as they are


def real_apis(conn: sqlite3.Connection) -> Apis:
    import httpx

    from reelline.config.credentials import ConfigError, load_youtube
    from reelline.pipeline.publish import CallLog
    from reelline.publish.buffer import BufferClient
    from reelline.publish.meta import FacebookClient
    from reelline.publish.youtube import YouTubeClient

    record = CallLog(conn, None)
    http = httpx.Client(timeout=conf().steps.analytics.http_timeout_s)
    out = Apis(None, None, None)
    with contextlib.suppress(ConfigError):
        out.buffer = BufferClient(record=record, http=http, on_limits=limits_sink(conn))
    with contextlib.suppress(ConfigError):
        out.meta = MetaStats(FacebookClient(record=record, http=http))
    try:
        out.youtube_settings = load_youtube()
        out.youtube = YouTubeClient(out.youtube_settings, http=http, record=record)
    except ConfigError:
        pass
    if "tiktok" in conf().steps.analytics.collectors:
        from reelline.stats.tiktok import TikTokClient

        with contextlib.suppress(ConfigError):
            out.tiktok = TikTokClient(http=http)
    return out


# ---- What's published ----------------------------------------------------------------------------


def published(conn: sqlite3.Connection) -> list[Pub]:
    rows = conn.execute("""SELECT p.*, r.duration_s FROM publications p JOIN reels r ON r.id = p.reel_id
                           WHERE p.state = 'published' AND p.published_at IS NOT NULL ORDER BY p.reel_id, p.id""")
    pubs = [
        Pub(
            r["id"],
            r["reel_id"],
            r["channel"],
            r["external_id"],
            r["url"],
            r["native_id"],
            datetime.fromisoformat(r["published_at"]),
            r["duration_s"],
            own_comments=int(r["channel"] == "facebook" and r["comment_state"] == "posted"),
        )
        for r in rows.fetchall()
    ]
    done: dict[int, set] = {}
    for r in conn.execute("SELECT publication_id, target_h FROM post_stats WHERE target_h IS NOT NULL").fetchall():
        done.setdefault(r[0], set()).add(r[1])
    for p in pubs:
        p.done = done.get(p.id, set())
    return pubs


def set_native(conn: sqlite3.Connection, pub: Pub, native_id: str) -> None:
    Ctx(conn, utcnow()).set_native(pub, native_id)


# ---- One fetch round -------------------------------------------------------------------------------


@dataclass
class Round:
    rows: int = 0
    followers: dict = field(default_factory=dict)
    skipped: dict = field(default_factory=dict)  # source -> what it needs (off)
    failed: dict = field(default_factory=dict)  # source -> error
    budget: str | None = None  # why Buffer was left alone (its budget)


def collect(
    conn: sqlite3.Connection,
    apis: Apis,
    pubs: list[Pub],
    now: datetime,
    *,
    followers: bool = False,
    say: Callable[[str], None] = lambda t: None,
    log: Callable[[str], None] = print,
    collectors: list | None = None,
) -> Round:
    """Fetch `pubs` from every source of every collector that's on (reelline/stats), store one
    merged row each (with the due snapshot point, if any), and the followers when asked."""
    out = Round()
    parts: dict[int, dict[str, dict]] = {p.id: {} for p in pubs}
    raw: dict[int, dict] = {p.id: {} for p in pubs}
    as_of: dict[int, datetime | None] = {}
    by_channel = {ch: [p for p in pubs if p.channel == ch] for ch in conf().steps.analytics.platforms}
    collectors = get_collectors(apis) if collectors is None else collectors
    ctx = Ctx(conn, now, log)

    def run(source: str, fn: Callable[[], None]) -> None:
        """One source's part; its health counts once per round (a failure wins)."""
        try:
            fn()
        except Exception as exc:  # one source never stops the others
            msg = f"{type(exc).__name__}: {exc}"[:400]
            log(f"stats: {source} failed: {msg}")
            if source not in out.failed:
                out.failed[source] = msg
                record_fail(conn, source, msg, now, say)
        else:
            if source not in out.failed:
                record_ok(conn, source, now, say)

    # What each collector needs first (Meta's scopes decide what's on).
    for c in collectors:
        ctx.info[c.name] = c.prepare(ctx)
    caps = capabilities(ctx.info.get("meta"), apis.youtube_settings)
    for c in collectors:
        caps.update(c.missing())
    for src, need in caps.items():
        if need:
            out.skipped[src] = need

    # The platforms' own ids, from the post URLs.
    for ch in ("youtube", "tiktok"):
        for p in by_channel.get(ch, ()):
            if not p.native_id and native_from_url(ch, p.url):
                ctx.set_native(p, native_from_url(ch, p.url))

    for c in collectors:
        for source in c.sources:
            if caps.get(source) or caps.get(c.depends.get(source)):
                continue
            mine = [p for ch in c.channels[source] for p in by_channel.get(ch, ()) if c.eligible(source, p, ctx)]
            if not mine:
                continue
            held = c.hold(source, ctx)
            if held:
                out.budget = held
                log(f"stats: {held}; {label(source).split(' ')[0]} skipped")
                continue

            def fetch(c=c, source=source, mine=mine):
                for p, part in c.collect(source, mine, ctx):
                    if part.fields is not None:
                        parts[p.id][source] = part.fields
                    raw[p.id].update(part.raw)
                    if part.as_of is not None:
                        as_of[p.id] = part.as_of

            if source in c.silent:
                fetch()
            else:
                run(source, fetch)
            c.finish(source, ctx)

    # One merged row per publication that any source answered for.
    fetched = db.now_iso(now)
    for p in pubs:
        if not parts[p.id]:
            continue
        fields, derived = merge(p.channel, parts[p.id], p.duration_s)
        direct = any(s in parts[p.id] for s in DIRECT)
        stamp = now if direct or as_of.get(p.id) is None else min(as_of[p.id], now)
        age = max(0.0, (stamp - p.published_at).total_seconds() / 3600)
        target = due_target(p.published_at, now, p.done)
        with db.transaction(conn):
            conn.execute(
                f"""INSERT INTO post_stats (publication_id, reel_id, channel, fetched_at, as_of, age_h, target_h, source,
                        {", ".join(METRICS)}, pct_derived, raw)
                    VALUES ({", ".join("?" * (8 + len(METRICS) + 2))})""",
                (
                    p.id,
                    p.reel_id,
                    p.channel,
                    fetched,
                    db.now_iso(stamp),
                    round(age, 2),
                    target,
                    "+".join(s for s in CHANNEL_SOURCES[p.channel] if s in parts[p.id]),
                    *(fields[k] for k in METRICS),
                    int(derived),
                    json.dumps(raw[p.id], default=str),
                ),
            )
        if target is not None:
            p.done.add(target)
        out.rows += 1

    if followers:
        out.followers = collect_followers(conn, ctx, collectors, caps, run)
    return out


def collect_followers(conn: sqlite3.Connection, ctx: Ctx, collectors: list, caps: dict, run) -> dict:
    """Followers per platform for today (Tbilisi), from whatever can read them."""
    got: dict[str, int] = {}
    day = ctx.now.astimezone(tbilisi()).date().isoformat()

    def store(f: Followers | None) -> None:
        if f is None or f.count is None:
            return
        got[f.platform] = f.count
        with db.transaction(conn):
            conn.execute(
                """INSERT INTO account_stats (platform, day, followers, fetched_at, source)
                            VALUES (?, ?, ?, ?, ?) ON CONFLICT (platform, day) DO UPDATE SET
                            followers = excluded.followers, fetched_at = excluded.fetched_at,
                            source = excluded.source""",
                (f.platform, day, f.count, db.now_iso(ctx.now), f.source),
            )

    for c in collectors:
        for source in c.follower_sources(ctx):
            if not caps.get(source):
                run(source, lambda c=c, source=source: store(c.followers(source, ctx)))
    return got


# ---- The tick's part ------------------------------------------------------------------------------


def sweep_due(conn: sqlite3.Connection, now: datetime) -> bool:
    local = now.astimezone(tbilisi())
    h, m = (int(x) for x in conf().steps.analytics.sweep_at.split(":"))
    return (
        local >= local.replace(hour=h, minute=m, second=0, microsecond=0)
        and db.get_state(conn, "stats_sweep_day") != local.date().isoformat()
    )


def tick(
    conn: sqlite3.Connection,
    now: datetime,
    say: Callable[[str], None],
    log: Callable[[str], None] = print,
    apis_factory: Callable[[sqlite3.Connection], Apis] | None = None,
) -> str | None:
    """The due snapshots (and once a day the sweep). Returns what it did, None for nothing."""
    apis_factory = apis_factory or real_apis
    pubs = published(conn)
    if not pubs:
        return None
    if sweep_due(conn, now):
        r = collect(conn, apis_factory(conn), pubs, now, followers=True, say=say, log=log)
        if r.rows or r.followers:
            db.set_state(conn, "stats_sweep_day", now.astimezone(tbilisi()).date().isoformat())
        return f"stats sweep: {r.rows} rows, followers {r.followers}" + round_notes(r)
    due = [p for p in pubs if due_target(p.published_at, now, p.done) is not None]
    if not due:
        return None
    r = collect(conn, apis_factory(conn), due, now, say=say, log=log)
    return f"stats: {r.rows} of {len(due)} due snapshots" + round_notes(r)


def round_notes(r: Round) -> str:
    return (f", failed {sorted(r.failed)}" if r.failed else "") + (f"; {r.budget}" if r.budget else "")


# ---- status -------------------------------------------------------------------------------------


def calls_per_day(conn: sqlite3.Connection, days: int = 7) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT substr(at, 1, 10) AS day,
                                  CASE WHEN action LIKE 'stats_buffer%' THEN 'buffer'
                                       WHEN action LIKE 'stats_yt%' THEN 'youtube'
                                       WHEN action LIKE 'stats_%' THEN 'graph'
                                       WHEN action LIKE 'buffer%' THEN 'buffer (publishing)'
                                       ELSE 'other (publishing)' END AS api, COUNT(*) AS n
                           FROM publish_calls WHERE at >= ? GROUP BY day, api ORDER BY day, api""",
        (db.now_iso(utcnow() - timedelta(days=days)),),
    ).fetchall()


def status_text(conn: sqlite3.Connection, now: datetime) -> str:
    from reelline.config.credentials import ConfigError, load_youtube

    try:
        yt = load_youtube()
    except ConfigError:
        yt = None
    caps = capabilities(meta_cache(conn), yt)
    hl = health(conn)
    lines = ["Sources:"]
    for s in SOURCES:
        h = hl.get(s)
        state = f"off - needs {caps[s]}" if caps[s] else "on"
        if h is not None and h["failures"]:
            state += f", failing x{h['failures']} since {h['failing_since']}: {h['error']}"
        elif h is not None and h["ok_at"]:
            state += f", last ok {h['ok_at']}"
        lines.append(f"  {s}: {state}")
    lim = buffer_limits(conn)
    if lim:
        lines.append(
            f"Buffer budget (seen {lim['seen_at']}): "
            + ", ".join(f"{k} {w['remaining']}/{w['limit']}" for k, w in lim["windows"].items())
        )
        blocked = buffer_blocked(lim, now)
        lines.append(f"  -> {blocked}" if blocked else "  -> stats may call Buffer")
    lines.append("API calls per day (UTC):")
    lines += [f"  {r['day']} {r['api']}: {r['n']}" for r in calls_per_day(conn)]
    n = conn.execute("SELECT COUNT(*) FROM post_stats").fetchone()[0]
    lines.append(
        f"post_stats rows: {n}; account_stats rows: {conn.execute('SELECT COUNT(*) FROM account_stats').fetchone()[0]}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m reelline.pipeline.stats", description="Read-only performance stats")
    sub = p.add_subparsers(dest="command", required=True)
    for name, help_ in (
        ("fetch", "the due snapshots now"),
        ("sweep", "every published reel + followers now"),
        ("backfill", "every published reel + followers now (the first run)"),
        ("status", "sources, health, Buffer's budget, calls per day"),
    ):
        sub.add_parser(name, help=help_)
    args = p.parse_args(argv)
    conn = db.connect(conf().storage.db_path)
    now = utcnow()
    if args.command == "status":
        print(status_text(conn, now))
        return 0
    if args.command == "fetch":
        print(tick(conn, now, say=print) or "nothing due")
        return 0
    pubs = published(conn)
    r = collect(conn, real_apis(conn), pubs, now, followers=True, say=print)
    print(f"{len(pubs)} publications: {r.rows} rows; followers {r.followers}")
    for s, why in sorted(r.skipped.items()):
        print(f"  off {s}: {why}")
    if r.budget:
        print(f"  skipped buffer: {r.budget}")
    for s, err in sorted(r.failed.items()):
        print(f"  FAILED {s}: {err}")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
