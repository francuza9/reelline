"""`python -m reelline.pipeline.costs`."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from reelline.config import conf
from reelline.pipeline import costs as _costs
from reelline.pipeline import db
from reelline.pipeline.costs.load import month_costs, report, sql_total
from reelline.pipeline.costs.months import CostsError, month_of, parse_month
from reelline.pipeline.costs.report import csv_text, format_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m reelline.pipeline.costs", description=(_costs.__doc__ or "").splitlines()[0]
    )
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("report", "the month's report"),
        ("csv", "every cost row of the month"),
        ("total", "the report's total and a direct SQL sum"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("month", nargs="?", help="YYYY-MM (default: this month, Tbilisi time)")
        if name == "csv":
            p.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)
    try:
        month = parse_month(args.month) if args.month else month_of(datetime.now(UTC))
    except CostsError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    conn = db.connect(args.db)
    if args.command == "report":
        print(format_report(report(conn, month)))
    elif args.command == "csv":
        costs, world = month_costs(conn, month)
        text = csv_text(costs, world)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            print(f"{len(costs)} rows -> {args.out}")
        else:
            sys.stdout.write(text)
    else:
        costs, _ = month_costs(conn, month)
        print(
            f"{month.key}: report total ${sum(c.usd for c in costs):.6f} ({len(costs)} rows), "
            f"SQL SUM(cost_usd) ${sql_total(conn, month):.6f}"
        )
    return 0
