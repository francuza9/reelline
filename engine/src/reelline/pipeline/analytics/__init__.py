"""The performance reports: /analytics in the bot (an overview for a period, or one
reel) and the evening summary's views line, from reelline/pipeline/stats.py's post_stats and
account_stats. steps.analytics in the channel config has the labels and the thresholds.

Usage:
  python -m reelline.pipeline.analytics report [7 | 30 | all]    the overview, as the bot sends it
  python -m reelline.pipeline.analytics reel <reel_id>            one reel

Definitions:
  - A period (Last 7 / 30 days) is Tbilisi days, today included; it holds the reels
    first published in it, with their latest numbers. All time: every reel.
  - A number is each publication's latest non-null value of it; totals add the
    platforms. Views are each platform's own count.
  - Engagement rate: (likes + comments + shares) / views.
  - Views at 24 h (the fair comparison): per platform the snapshot whose data is
    nearest 24 h old within steps.analytics.fair_window_h, added over the platforms; a
    reel counts only when every platform with numbers has one.
  - Slot: the nearest of steps.analytics.report_slots (every slot time the channel has
    posted at) to the first publication, Tbilisi time; further than
    steps.analytics.slot_tolerance_min from all of them is "other".
  - A topic / slot split shows only when at least two groups have
    steps.analytics.min_split_reels reels each. The cold open split (reels.gag, reelline/media/gags.py): the
    A/B's reels (gag_mode ab) with a gag against those without, listed once the period has any.
  - Opening split (reels.opening_version, the version before OPENING_VERSION vs it):
    per platform, the median watch time, % viewed and views at 24 h of each version's
    reels with steps.analytics.watch_min_views+ views on that platform; shown only when
    both versions have steps.analytics.opening_min_reels such reels there, else "not
    enough data yet".
  - Cost per 1,000 views: the period's spend (llm_calls, as /costs counts it) over the
    views of the period's reels.
  - % viewed marked ≈ is derived: average watch time / the reel's length. One reel's
    watch time and % viewed are the platform's own, as given; past 100% (replays count
    in watch time) they get steps.analytics.replay_mark.
  - Watch time and % viewed over several reels (a platform's line, a split's group):
    the median, of the reels with steps.analytics.watch_min_views+ views on that platform;
    the footer says so.

Pure functions (period_start(), slot_of(), build_overview(), format_overview(),
build_reel(), format_reel(), morning_line()) over Data; load() is the only database read.
"""

from __future__ import annotations

from reelline.pipeline.analytics.cli import (
    main,
    reel_cost,
)
from reelline.pipeline.analytics.data import (
    PLATFORMS,
    AnalyticsError,
    Data,
    Pub,
    Reel,
    Stat,
    load,
)
from reelline.pipeline.analytics.helpers import (
    curve_point,
    curve_window,
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
    views_near,
    watch_counted,
)
from reelline.pipeline.analytics.overview import (
    OpeningLine,
    OpeningSide,
    Overview,
    PlatformLine,
    Split,
    build_overview,
    followers_change,
    footer,
    format_overview,
    missing_notes,
    opening_split,
    split,
    watch_note,
)
from reelline.pipeline.analytics.reel import (
    ReelReport,
    build_reel,
    format_reel,
    morning_line,
    views_per_dollar,
)

__all__ = [
    "PLATFORMS",
    "AnalyticsError",
    "Data",
    "OpeningLine",
    "OpeningSide",
    "Overview",
    "PlatformLine",
    "Pub",
    "Reel",
    "ReelReport",
    "Split",
    "Stat",
    "build_overview",
    "build_reel",
    "curve_point",
    "curve_window",
    "followers_change",
    "footer",
    "format_overview",
    "format_reel",
    "label",
    "latest",
    "load",
    "local",
    "main",
    "missing_notes",
    "morning_line",
    "num",
    "opening_split",
    "pct",
    "pct_is_derived",
    "period_start",
    "reel_cost",
    "reel_pct",
    "reel_views_at",
    "secs",
    "shorten",
    "slot_of",
    "spend_in",
    "split",
    "total",
    "views_near",
    "views_per_dollar",
    "watch_counted",
    "watch_note",
]
