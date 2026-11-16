"""`python -m reelline.pipeline.analytics`."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.analytics.data import AnalyticsError, load
from reelline.pipeline.analytics.overview import build_overview, format_overview
from reelline.pipeline.analytics.reel import build_reel, format_reel


def reel_cost(conn: sqlite3.Connection, reel_id: int) -> dict | None:
    from reelline.pipeline import costs

    try:
        return costs.reel_parts(costs.reel_costs(conn, reel_id))
    except costs.CostsError:
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m reelline.pipeline.analytics", description="Performance reports")
    sub = p.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("report", help="the overview")
    rp.add_argument("period", nargs="?", default="7", choices=list(conf().steps.analytics.periods))
    rl = sub.add_parser("reel", help="one reel")
    rl.add_argument("reel_id", type=int)
    args = p.parse_args(argv)
    conn = db.connect(conf().storage.db_path)
    now = datetime.now(UTC)
    data = load(conn, now)
    try:
        if args.command == "report":
            print(format_overview(build_overview(data, args.period)))
        else:
            print(format_reel(build_reel(data, args.reel_id, reel_cost(conn, args.reel_id))))
    except AnalyticsError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
