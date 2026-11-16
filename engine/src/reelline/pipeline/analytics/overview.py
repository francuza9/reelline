"""The overview: per platform, splits by slot and opening, the formatted text."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime

from reelline.config import conf
from reelline.pipeline.analytics.data import PLATFORMS, Data, Reel, _dt
from reelline.pipeline.analytics.helpers import (
    curve_point,
    label,
    latest,
    local,
    num,
    pct,
    pct_is_derived,
    period_start,
    reel_pct,
    reel_views_at,
    secs,
    shorten,
    slot_of,
    spend_in,
    total,
    watch_counted,
)
from reelline.pipeline.scoring import tbilisi


@dataclass
class PlatformLine:
    platform: str
    reels: int
    views: int | None
    likes: int
    comments: int
    shares: int
    per_reel: float | None
    engagement: float | None
    watch_s: float | None  # median over watch_n reels (watch_counted)
    pct: float | None
    pct_derived: bool
    watch_n: int = 0
    watch_left_out: int = 0  # reels with watch time but under watch_min_views views


@dataclass
class Split:
    name: str
    groups: list[tuple[str, int, float | None, float | None]]  # (group, reels, avg 24 h views, median % viewed)
    enough: bool


@dataclass
class OpeningSide:
    version: int
    reels: int  # reels of this version with watch_min_views+ views on the platform
    watch_s: float | None  # medians over those reels
    pct: float | None
    pct_derived: bool
    views24: float | None


@dataclass
class OpeningLine:
    platform: str
    sides: list[OpeningSide]  # (previous version, current version)
    enough: bool


@dataclass
class Overview:
    key: str
    start: datetime | None
    now: datetime
    reels: list[Reel]
    totals: dict
    followers: list[tuple[str, int | None, int | None]]  # (platform, now, change)
    platforms: list[PlatformLine]
    best_platform: str | None
    top: list[Reel]
    median24: float | None
    n24: int
    splits: list[Split]
    opening: list[OpeningLine]
    spend: float
    per_1k: float | None
    updated: datetime | None
    notes: list[str]


def followers_change(data: Data, platform: str, start: datetime | None) -> tuple[int | None, int | None]:
    rows = data.followers.get(platform) or []
    if not rows:
        return None, None
    now_n = rows[-1][1]
    if start is None:
        base = rows[0][1]
    else:
        first_day = start.astimezone(tbilisi()).date().isoformat()
        before = [n for d, n in rows if d < first_day]
        base = before[-1] if before else rows[0][1]
    return now_n, (now_n - base if len(rows) > 1 else None)


def split(name: str, reels: list[Reel], key) -> Split:
    groups: dict[str, list[Reel]] = {}
    for r in reels:
        groups.setdefault(key(r) or "?", []).append(r)
    rows = []
    for g, rs in groups.items():
        v24 = [v for v in (reel_views_at(r) for r in rs) if v is not None]
        pcts = [x for x in (reel_pct(r) for r in rs) if x is not None]
        rows.append((g, len(v24), statistics.mean(v24) if v24 else None, statistics.median(pcts) if pcts else None))
    rows.sort(key=lambda g: (-(g[2] or -1), g[0]))
    enough = sum(1 for g in rows if g[1] >= conf().steps.analytics.min_split_reels) >= 2
    return Split(name, rows, enough)


def opening_split(
    reels: list[Reel], versions: tuple[int, int] = (conf().render.opening_version - 1, conf().render.opening_version)
) -> list[OpeningLine]:
    """Per platform with publications, each opening version's medians over its reels with
    watch_min_views+ views there; enough when both have opening_min_reels of them."""
    out = []
    for pf in PLATFORMS:
        pubs = [(r.opening_version, p) for r in reels for p in r.pubs if p.channel == pf]
        if not pubs:
            continue
        sides = []
        for v in versions:
            counted = watch_counted([p for ver, p in pubs if ver == v])
            watch = [latest(p, "avg_watch_s") for p in counted if latest(p, "avg_watch_s") is not None]
            pcts = [(latest(p, "avg_pct"), pct_is_derived(p)) for p in counted if latest(p, "avg_pct") is not None]
            v24 = [x for x in (curve_point(p, conf().steps.analytics.fair_age_h) for p in counted) if x is not None]
            sides.append(
                OpeningSide(
                    v,
                    len(counted),
                    statistics.median(watch) if watch else None,
                    statistics.median(x for x, _ in pcts) if pcts else None,
                    any(d for _, d in pcts),
                    statistics.median(v24) if v24 else None,
                )
            )
        out.append(OpeningLine(pf, sides, all(s.reels >= conf().steps.analytics.opening_min_reels for s in sides)))
    return out


def missing_notes(data: Data) -> list[str]:
    caps = data.caps
    via = [
        label(pf)
        for pf, direct in (("instagram", "instagram_graph"), ("tiktok", None), ("youtube", "youtube_data"))
        if direct is None or caps.get(direct)
    ]
    out = [f"{', '.join(via)} views via Buffer (it refreshes them about daily)."] if via else []
    what = {
        "facebook_insights": "Facebook watch time, reach, shares",
        "instagram_graph": "Instagram watch time, followers",
        "youtube_data": "YouTube subscribers (views via Buffer)",
        "youtube_analytics": "YouTube watch time, % viewed, shares",
    }
    for src, metrics in what.items():
        if caps.get(src):
            out.append(f"{metrics}: n/a (needs {caps[src]})")
    out.append(f"TikTok followers: n/a ({conf().steps.analytics.needs['tiktok_followers']})")
    from reelline.pipeline.stats import label as source_label

    for src, (n, err, since) in sorted(data.failing.items()):
        when = local(_dt(since), "%b %d %H:%M") if since else "?"
        out.append(f"⚠️ {source_label(src)} n/a: failing since {when} ({n}x): {shorten(err or '', 120)}")
    if data.buffer_block:
        out.append(f"⏸ Buffer stats paused: {data.buffer_block}")
    return out


def build_overview(data: Data, key: str) -> Overview:
    start = period_start(key, data.now)
    reels = sorted((r for r in data.reels.values() if start is None or r.posted_at >= start), key=lambda r: r.posted_at)
    totals = {m: sum(total(r, m) for r in reels) for m in ("views", "likes", "comments", "shares")}
    lines = []
    for pf in PLATFORMS:
        pubs = [p for r in reels for p in r.pubs if p.channel == pf]
        if not pubs:
            continue
        sums = {m: sum(latest(p, m) or 0 for p in pubs) for m in ("views", "likes", "comments", "shares")}
        with_views = [p for p in pubs if latest(p, "views") is not None]
        timed = [p for p in pubs if latest(p, "avg_watch_s") is not None or latest(p, "avg_pct") is not None]
        counted = watch_counted(timed)
        watch = [latest(p, "avg_watch_s") for p in counted if latest(p, "avg_watch_s") is not None]
        pcts = [(latest(p, "avg_pct"), pct_is_derived(p)) for p in counted if latest(p, "avg_pct") is not None]
        lines.append(
            PlatformLine(
                pf,
                len(pubs),
                sums["views"] if with_views else None,
                sums["likes"],
                sums["comments"],
                sums["shares"],
                sums["views"] / len(with_views) if with_views else None,
                (sums["likes"] + sums["comments"] + sums["shares"]) / sums["views"] if sums["views"] else None,
                statistics.median(watch) if watch else None,
                statistics.median(x for x, _ in pcts) if pcts else None,
                any(d for _, d in pcts),
                len(counted),
                len(timed) - len(counted),
            )
        )
    ranked = [ln for ln in lines if ln.per_reel is not None]
    best = max(ranked, key=lambda ln: ln.per_reel).platform if len(ranked) > 1 else None
    top = sorted(reels, key=lambda r: -total(r, "views"))[: conf().steps.analytics.top_n]
    v24 = [v for v in (reel_views_at(r) for r in reels) if v is not None]
    splits = [split("By topic", reels, lambda r: r.topic), split("By slot", reels, lambda r: slot_of(r.posted_at))]
    ab = [r for r in reels if r.gag_mode == "ab"]
    if ab:  # the cold open's A/B (reelline/media/gags.py): its reels with a gag against those without
        splits.append(split("By cold open (A/B)", ab, lambda r: "gag" if r.gag else "no gag"))
    spend = spend_in(data, start, data.now)
    fetched = [s.fetched_at for r in reels for p in r.pubs for s in p.stats]
    return Overview(
        key,
        start,
        data.now,
        reels,
        totals,
        [(pf, *followers_change(data, pf, start)) for pf in PLATFORMS],
        lines,
        best,
        top,
        statistics.median(v24) if v24 else None,
        len(v24),
        splits,
        opening_split(reels),
        spend,
        spend / totals["views"] * 1000 if totals["views"] else None,
        max(fetched) if fetched else None,
        ([watch_note()] if reels else []) + missing_notes(data),
    )


def watch_note() -> str:
    return (
        f"Watch time and % viewed: the median over reels with {conf().steps.analytics.watch_min_views}+ views on that "
        f"platform (fewer views: left out). {conf().steps.analytics.replay_mark} = over 100%: replays count in watch time."
    )


def format_overview(o: Overview) -> str:
    title = conf().steps.analytics.periods[o.key]
    if o.start is not None:
        span = f"{local(o.start, '%b %d')} - {local(o.now, '%b %d')}, Tbilisi; reels posted in it"
    else:
        span = "every reel"
    out = [f"📊 Analytics: {title} ({span})"]
    if not o.reels:
        out += ["", "No published reels in this period."]
        return "\n".join(out + [""] + footer(o.updated, o.notes))
    t = o.totals
    out.append(
        f"{len(o.reels)} reel{'s' * (len(o.reels) != 1)} · {num(t['views'])} views · {num(t['likes'])} likes · "
        f"{num(t['comments'])} comments · {num(t['shares'])} shares"
    )

    out += ["", "Followers"]
    parts = []
    for pf, n, change in o.followers:
        if n is None:
            parts.append(f"{label(pf)} n/a")
        else:
            parts.append(f"{label(pf)} {num(n)}" + (f" ({change:+d})" if change is not None else ""))
    out.append(" · ".join(parts))

    out += ["", "Platforms"]
    for ln in o.platforms:
        star = "⭐ " if ln.platform == o.best_platform else ""
        n = f"{ln.reels} reel{'s' * (ln.reels != 1)}"
        if ln.views is None:
            out.append(f"{label(ln.platform)}: {n} · no numbers yet")
            continue
        eng = f"eng {ln.engagement:.1%}" if ln.engagement is not None else "eng n/a"
        if ln.watch_n:
            watch = (
                f"watch {secs(ln.watch_s)}"
                + (f" ({pct(ln.pct, ln.pct_derived)} viewed)" if ln.pct is not None else "")
                + f", median of {ln.watch_n}"
            )
        elif ln.watch_left_out:
            watch = f"watch n/a (no reel with {conf().steps.analytics.watch_min_views}+ views)"
        else:
            watch = "watch n/a"
        out.append(
            f"{star}{label(ln.platform)}: {n} · {num(ln.views)} views ({num(ln.per_reel)}/reel) · {eng} · {watch}"
        )
        out.append(f"   {num(ln.likes)} likes · {num(ln.comments)} comments · {num(ln.shares)} shares")
    if o.best_platform:
        out.append("⭐ = most views per reel")

    out += ["", f"Best posts (top {len(o.top)} by views)"]
    for i, r in enumerate(o.top, 1):
        per = " · ".join(f"{conf().steps.analytics.short[p.channel]} {num(latest(p, 'views'))}" for p in r.pubs)
        best = max(r.pubs, key=lambda p: latest(p, "views") or -1)
        out.append(
            f"{i}. #{r.id} {local(r.posted_at)} ({slot_of(r.posted_at)}) · "
            f"{shorten(r.title, conf().steps.analytics.title_chars)}"
        )
        out.append(f"   {num(total(r, 'views'))} views ({per})" + (f" · {best.url}" if best.url else ""))

    out += ["", f"Views at {conf().steps.analytics.fair_age_h} h (fair comparison)"]
    if o.median24 is None:
        out.append("No reel has its 24 h numbers yet.")
    else:
        out.append(f"Median {num(o.median24)} per reel over {o.n24} reel{'s' * (o.n24 != 1)}")

    out += ["", f"What works (avg views at {conf().steps.analytics.fair_age_h} h, median % viewed)"]
    for s in o.splits:
        counts = ", ".join(f"{g} {n}" for g, n, _, _ in sorted(s.groups))
        if not s.enough:
            out.append(
                f"{s.name}: not enough data yet ({counts}; needs {conf().steps.analytics.min_split_reels}+ reels "
                "in two groups)"
            )
            continue
        out.append(f"{s.name}:")
        for g, n, v, p in s.groups:
            if n >= conf().steps.analytics.min_split_reels:
                out.append(f"   {g}: {num(v)} ({n} reels" + (f", {pct(p)} viewed" if p is not None else "") + ")")
            else:
                out.append(f"   {g}: too few ({n})")

    if o.opening:
        old, new = (f"v{s.version}" for s in o.opening[0].sides)
        out += [
            "",
            f"Opening {old} vs {new} (median watch, % viewed, views at {conf().steps.analytics.fair_age_h} h; "
            f"reels with {conf().steps.analytics.watch_min_views}+ views)",
        ]
        for ln in o.opening:
            counts = ", ".join(f"v{s.version} {s.reels}" for s in ln.sides)
            if not ln.enough:
                out.append(
                    f"{label(ln.platform)}: not enough data yet ({counts}; needs "
                    f"{conf().steps.analytics.opening_min_reels}+ each)"
                )
                continue
            out.append(f"{label(ln.platform)}:")
            for s in ln.sides:
                out.append(
                    f"   v{s.version}: watch {secs(s.watch_s)} · {pct(s.pct, s.pct_derived)} viewed · "
                    f"{num(s.views24)} views at {conf().steps.analytics.fair_age_h} h ({s.reels} reels)"
                )

    out += ["", "Cost"]
    out.append(
        f"{'$'}{o.spend:,.2f} spent in the period · "
        + (f"${o.per_1k:,.2f} per 1,000 views" if o.per_1k is not None else "per 1,000 views n/a (no views)")
    )
    return "\n".join(out + [""] + footer(o.updated, o.notes))


def footer(updated: datetime | None, notes: list[str]) -> list[str]:
    return [f"Updated {local(updated, '%b %d %H:%M') if updated else 'never'} (Tbilisi)."] + notes
