"""One reel's report and the views line of the evening summary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.pipeline.analytics.data import PLATFORMS, AnalyticsError, Data, Reel
from reelline.pipeline.analytics.helpers import (
    curve_point,
    label,
    latest,
    local,
    num,
    pct,
    pct_is_derived,
    reel_views_at,
    secs,
    shorten,
    slot_of,
    total,
)
from reelline.pipeline.analytics.overview import missing_notes
from reelline.pipeline.scoring import tbilisi


@dataclass
class ReelReport:
    reel: Reel
    cost: dict | None
    now: datetime
    notes: list[str]


def build_reel(data: Data, reel_id: int, cost: dict | None = None) -> ReelReport:
    reel = data.reels.get(reel_id)
    if reel is None:
        raise AnalyticsError(f"Reel #{reel_id} has no published post")
    return ReelReport(reel, cost, data.now, missing_notes(data))


def format_reel(rr: ReelReport) -> str:
    r = rr.reel
    out = [
        f"📊 Reel #{r.id} · {shorten(r.title, 60)}",
        f"Posted {local(r.posted_at)} ({slot_of(r.posted_at)}) · {r.topic or '?'}"
        + (f" · {r.duration_s:.0f} s" if r.duration_s else ""),
    ]
    out.append(
        f"All platforms: {num(total(r, 'views'))} views · {num(total(r, 'likes'))} likes · "
        f"{num(total(r, 'comments'))} comments · {num(total(r, 'shares'))} shares"
    )
    out.append("")
    for p in r.pubs:
        if not p.stats:
            out.append(f"{label(p.channel)}: no stats yet")
            continue
        bits = [
            f"{num(latest(p, 'views'))} views",
            f"{num(latest(p, 'likes'))} likes",
            f"{num(latest(p, 'comments'))} comments",
            f"{num(latest(p, 'shares'))} shares",
        ]
        for m, name in (("saves", "saves"), ("reach", "reach"), ("follows", "follows")):
            if latest(p, m) is not None:
                bits.append(f"{num(latest(p, m))} {name}")
        watch = latest(p, "avg_watch_s")
        bits.append(
            f"watch {secs(watch)}"
            + (f" ({pct(latest(p, 'avg_pct'), pct_is_derived(p))} viewed)" if latest(p, "avg_pct") is not None else "")
        )
        out.append(f"{label(p.channel)}: " + " · ".join(bits) + f" · as of {local(p.stats[-1].as_of, '%b %d %H:%M')}")
    out += ["", "Views curve: " + " · ".join(lab for _, lab in conf().steps.analytics.curve_h)]
    for p in r.pubs:
        out.append(
            f"   {conf().steps.analytics.short[p.channel]}: "
            + " · ".join(
                num(curve_point(p, t)) if curve_point(p, t) is not None else "-"
                for t, _ in conf().steps.analytics.curve_h
            )
        )
    alls = [reel_views_at(r, t) for t, _ in conf().steps.analytics.curve_h]
    out.append("   All: " + " · ".join(num(v) if v is not None else "-" for v in alls))
    if any((latest(p, "avg_pct") or 0) > 100 for p in r.pubs):
        out.append(
            f"{conf().steps.analytics.replay_mark} = over 100% viewed: replays count in the platform's watch time"
        )
    out += ["", "Links"]
    out += [f"{label(p.channel)}: {p.url}" for p in r.pubs if p.url]
    if rr.cost is not None:
        parts = ", ".join(f"{lab} ${x:.2f}" for lab, x in rr.cost["parts"] if x)
        out += [
            "",
            f"Cost: ${rr.cost['total']:.2f}"
            + (f" ({parts})" if parts else "")
            + (f"; {views_per_dollar(total(r, 'views'), rr.cost['total'])}" if rr.cost["total"] else ""),
        ]
    return "\n".join(out + [""] + rr.notes)


def views_per_dollar(views: int, usd: float) -> str:
    return f"${usd / views * 1000:,.2f} per 1,000 views" if views else "no views yet"


def morning_line(data: Data) -> str:
    """Views over the last 24 h (all reels) and the followers' change since yesterday."""
    since = data.now - timedelta(hours=24)
    per: dict[str, int] = {}
    known = False
    for r in data.reels.values():
        for p in r.pubs:
            now_v = latest(p, "views")
            if now_v is None:
                continue
            if p.published_at >= since:
                base = 0
            else:
                before = [s.values["views"] for s in p.stats if s.as_of <= since and s.values.get("views") is not None]
                if not before:
                    continue
                base = before[-1]
            known = True
            per[p.channel] = per.get(p.channel, 0) + max(0, now_v - base)
    today = data.now.astimezone(tbilisi()).date().isoformat()
    changes = {}
    for pf, rows in data.followers.items():
        cur = [n for d, n in rows if d == today]
        prev = [n for d, n in rows if d < today]
        if cur and prev:
            changes[pf] = cur[-1] - prev[-1]
    if not known and not changes:
        return "📈 Views: no stats yet (/analytics)"
    views = f"📈 Views, last 24 h: {num(sum(per.values()))}" + (
        " (" + " · ".join(f"{conf().steps.analytics.short[pf]} {num(per[pf])}" for pf in PLATFORMS if pf in per) + ")"
        if per
        else ""
    )
    if changes:
        views += (
            f"; followers {sum(changes.values()):+d} ("
            + " · ".join(f"{conf().steps.analytics.short[pf]} {changes[pf]:+d}" for pf in PLATFORMS if pf in changes)
            + ")"
        )
    return views
