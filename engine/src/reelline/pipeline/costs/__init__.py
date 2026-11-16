"""The monthly cost report: every paid call in llm_calls (the cost ledger since
migration 11), for one Asia/Tbilisi calendar month.

Usage:
  python -m reelline.pipeline.costs report [YYYY-MM]
      The report, as /costs in the approval bot sends it (default: this month).
  python -m reelline.pipeline.costs csv [YYYY-MM] [-o FILE]
      Every cost row of the month (costs.csv_columns), to stdout or FILE.
  python -m reelline.pipeline.costs total [YYYY-MM]
      The month's total two ways: the report's, and a direct SQL sum of llm_calls.

Months run from midnight on the 1st, Tbilisi time (reelline/pipeline/scoring.py tbilisi()),
so a call at 23:30 UTC on the last day of a month is in the next month.

The report: the total and the change against the month before (for the current
month also the spend so far, the days elapsed and a pace estimate: the daily average
so far times the days in the month; and, once a reel is approved, a projection at the
schedule's reels a day: the spend so far plus the days left times len(channel.slots)
times the month's cost per approved reel); by provider (Google split into Gemini text,
vision, image generation, TTS); by step; by outcome; the top reels; the daily totals.
Labels, steps and groupings are costs in the channel config. A try whose connection
dropped (llm_calls.error, reelline/providers/retry.py) counts like its call, under its step; the
footer's "🔁 Retries" line sums them (the tokens known when each dropped, so a lower
bound; a Gemini try that dropped has none, and isn't listed as unpriced).

Every row counts once, under one outcome:
  - manual (a dev CLI: scripts/*.py, `qa run`): "Manual (dev)";
  - assess (picks are made from many stories at once): "Overhead (assess)";
  - a row with a reel (the render's TTS, images, QA): that reel;
  - a script's row (story, punch-up, fact-check, caption): the script's last reel, so
    with New voice / New images the script counts under the final reel, and with New
    script the old script's spend goes with its own (superseded) reel;
  - a pick's row (the brief): the pick's last reel.
A reel's status gives the outcome (costs.reel_outcomes): approved / published
delivered; rejected, superseded, expired, render_failed waste; rendered (in review)
pending. Spend that never got a reel is waste (the pick skipped or dropped, the script
failed or was replaced, the job failed) or, while its pick is still live, pending.
Cost per approved reel is the month's spend over the reels approved (created in
the month): waste and overhead included, manual dev spend (trials, test renders, the
supply check) left out, so test runs never count as a reel's cost. A reel run is the spend tied to that reel
(its render, TTS, images and QA); the median is over the month's reels. A reel's
total (the top reels; the review's "Cost so far", reel_costs()) is everything that
counts under it, manual dev spend left out.

Pure functions (attribute(), build_report(), format_report(), csv_text()) over plain
rows; load() is the only database read.
"""

from __future__ import annotations

from reelline.pipeline.costs.cli import (
    main,
)
from reelline.pipeline.costs.load import (
    day_total,
    load_rows,
    load_world,
    month_costs,
    reel_costs,
    reel_parts,
    report,
    sql_total,
)
from reelline.pipeline.costs.model import (
    Cost,
    Row,
    World,
    attribute,
    counts_for_reel,
    matches,
    outcome_of,
    provider_of,
    step_of,
)
from reelline.pipeline.costs.months import (
    CostsError,
    Month,
    local,
    month_of,
    parse_month,
    recent_months,
)
from reelline.pipeline.costs.report import (
    Report,
    build_report,
    csv_text,
    footer,
    format_report,
    share,
    shorten,
    usd,
)

__all__ = [
    "Cost",
    "CostsError",
    "Month",
    "Report",
    "Row",
    "World",
    "attribute",
    "build_report",
    "counts_for_reel",
    "csv_text",
    "day_total",
    "footer",
    "format_report",
    "load_rows",
    "load_world",
    "local",
    "main",
    "matches",
    "month_costs",
    "month_of",
    "outcome_of",
    "parse_month",
    "provider_of",
    "recent_months",
    "reel_costs",
    "reel_parts",
    "report",
    "share",
    "shorten",
    "sql_total",
    "step_of",
    "usd",
]
