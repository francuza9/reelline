"""The ingest command line."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.ingest.base import IngestError
from reelline.pipeline.ingest.commands import cmd_report, cmd_reset, cmd_run, cmd_sources
from reelline.pipeline.sources import SourceRegistryError, load_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m reelline.pipeline.ingest", description="Collect new stories from sources.yaml."
    )
    parser.add_argument("--source", action="append", metavar="ID", help="only this source (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and extract, write nothing")
    parser.add_argument("--since-days", type=int, metavar="N", help="keep only items from the last N days")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any source failed")
    parser.add_argument("--reset", action="append", metavar="ID", help="re-enable a source disabled in the database")
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=conf().sources, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")
    p_sources = sub.add_parser("sources", help="list the registry; --check fetches every source live")
    p_sources.add_argument("--check", action="store_true")
    sub.add_parser("report", help="per-source health and story counts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_options = args.source or args.dry_run or args.since_days is not None or args.strict
    if args.command and (run_options or args.reset):
        parser.error(f"--source, --dry-run, --since-days, --strict and --reset don't go with `{args.command}`")
    if args.reset and run_options:
        parser.error("--reset runs on its own")
    try:
        sources = load_sources(args.registry)
        if args.command == "sources":
            return cmd_sources(args, sources)
        if args.command == "report":
            return cmd_report(args, sources)
        if args.reset:
            return cmd_reset(args, sources)
        return cmd_run(args, sources)
    except (IngestError, SourceRegistryError, db.DbError, sqlite3.Error) as exc:
        print(
            f"error: {type(exc).__name__}: {exc}" if isinstance(exc, sqlite3.Error) else f"error: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
