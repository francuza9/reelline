"""Small pure helpers: periods, slots, curve points, formatting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from reelline.config import conf
from reelline.pipeline.analytics.data import AnalyticsError, Data, Pub, Reel
from reelline.pipeline.scoring import tbilisi


def period_start(key: str, now: datetime) -> datetime | None:
    """The period's first instant (UTC): Tbilisi midnight `days - 1` days before today;
    None for all time."""
    if key == "all":
        return None
    if key not in conf().steps.analytics.periods:
        raise AnalyticsError(f"Unknown period {key!r}: one of {', '.join(conf().steps.analytics.periods)}")
    today = now.astimezone(tbilisi()).date()
    first = today - timedelta(days=int(key) - 1)
    return datetime(first.year, first.month, first.day, tzinfo=tbilisi()).astimezone(UTC)


def slot_of(at: datetime) -> str:
    local = at.astimezone(tbilisi())
    minutes = local.hour * 60 + local.minute
    best, gap = None, None
    for s in conf().steps.analytics.report_slots:
        h, m = (int(x) for x in s.split(":"))
        d = abs(minutes - (h * 60 + m))
        d = min(d, 24 * 60 - d)
        if gap is None or d < gap:
            best, gap = s, d
    return (
        best
        if gap is not None and gap <= conf().steps.analytics.slot_tolerance_min
        else conf().steps.analytics.other_slot
    )


def latest(pub: Pub, metric: str):
    """The publication's latest non-null value of `metric` (None: never given)."""
    for s in reversed(pub.stats):
        if s.values.get(metric) is not None:
            return s.values[metric]
    return None


def pct_is_derived(pub: Pub) -> bool:
    for s in reversed(pub.stats):
        if s.values.get("avg_pct") is not None:
            return s.pct_derived
    return False


def views_near(pub: Pub, age_h: float, window: tuple[float, float]) -> int | None:
    """Views from the snapshot whose data is nearest `age_h` old within `window`."""
    best = None
    for s in pub.stats:
        if s.values.get("views") is None or not window[0] <= s.age_h <= window[1]:
            continue
        if best is None or abs(s.age_h - age_h) < abs(best.age_h - age_h):
            best = s
    return None if best is None else best.values["views"]


def curve_window(t: float) -> tuple[float, float]:
    """The data ages that count as the point t (steps.analytics.curve_windows_h; 24 h is
    the fair comparison's window)."""
    if t == conf().steps.analytics.fair_age_h:
        return tuple(conf().steps.analytics.fair_window_h)
    return tuple(conf().steps.analytics.curve_windows_h.get(t, (t * 0.75, t * 1.5)))


def curve_point(pub: Pub, t: float) -> int | None:
    """Views at the point t: the snapshot whose data is nearest t old within its
    window (a late snapshot filling the point doesn't count when its data is older)."""
    return views_near(pub, t, curve_window(t))


def reel_views_at(reel: Reel, t: float | None = None) -> int | None:
    """The reel's views at the point t over its platforms; None unless every platform
    with numbers has one for t (a partial sum isn't the reel's views)."""
    t = t or conf().steps.analytics.fair_age_h
    with_views = [p for p in reel.pubs if any(s.values.get("views") is not None for s in p.stats)]
    vals = [curve_point(p, t) for p in with_views]
    return sum(vals) if vals and all(v is not None for v in vals) else None


def total(reel: Reel, metric: str) -> int:
    return sum(latest(p, metric) or 0 for p in reel.pubs)


def watch_counted(pubs: list[Pub]) -> list[Pub]:
    """The publications whose watch time counts in a median: steps.analytics.watch_min_views+ views."""
    return [p for p in pubs if (latest(p, "views") or 0) >= conf().steps.analytics.watch_min_views]


def reel_pct(reel: Reel) -> float | None:
    """The reel's % viewed: its platforms with watch_counted() views, weighted by them."""
    num = den = 0.0
    for p in watch_counted(reel.pubs):
        pct, views = latest(p, "avg_pct"), latest(p, "views")
        if pct is not None and views:
            num += pct * views
            den += views
    return num / den if den else None


def spend_in(data: Data, start: datetime | None, end: datetime) -> float:
    return sum(usd for at, usd in data.spend if (start is None or at >= start) and at < end)


def num(n: int | float | None) -> str:
    if n is None:
        return "n/a"
    return f"{n:,.0f}" if abs(n) >= 1 or n == 0 else f"{n:.1f}"


def pct(x: float | None, derived: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:.0f}%{'≈' if derived else ''}" + (f" {conf().steps.analytics.replay_mark}" if x > 100 else "")


def secs(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f} s"


def shorten(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def local(at: datetime, fmt: str = "%b %d %H:%M") -> str:
    return at.astimezone(tbilisi()).strftime(fmt)


def label(platform: str) -> str:
    return conf().steps.analytics.labels[platform]
