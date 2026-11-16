"""Command line: run the checks on an existing render."""

from __future__ import annotations

from pathlib import Path

from reelline.config import conf
from reelline.pipeline.qa.base import QaError, subject_from_dir
from reelline.pipeline.qa.report import caption_fields, record_calls, report_text, script_for_reel
from reelline.pipeline.qa.vision import Checker


def cmd_run(args) -> int:
    from reelline.pipeline import db
    from reelline.pipeline.sources import load_sources

    reel_dir = args.reel_dir.resolve()
    if not (reel_dir / "reel.mp4").is_file():
        raise QaError(f"No reel.mp4 in {reel_dir}")
    conn = db.connect(args.db)
    script = script_for_reel(conn, reel_dir.name)
    fields = {}
    if script is None:
        print(f"NOTE: no script for {reel_dir.name} in the database; the post caption checks are skipped")
    else:
        names = {s.id: s.name for s in load_sources(args.registry)}
        reel = conn.execute("SELECT id FROM reels WHERE name = ? ORDER BY id DESC LIMIT 1", (reel_dir.name,)).fetchone()
        fields = caption_fields(conn, script["id"], names, reel_id=reel["id"] if reel else None)
    from reelline.providers.costs import recording

    reel = conn.execute("SELECT id FROM reels WHERE name = ? ORDER BY id DESC LIMIT 1", (reel_dir.name,)).fetchone()
    # A dev check, not part of a reel's run: tagged manual, written as each call returns.
    with recording(
        db.live_recorder(
            conn, script_id=script["id"] if script else None, reel_id=reel["id"] if reel else None, manual=True
        )
    ):
        report = Checker().run(subject_from_dir(reel_dir, **fields))
    if report.calls:
        record_calls(conn, report.calls, script["id"] if script else None, reel["id"] if reel else None, manual=True)
    print(f"QA for {reel_dir.name} (report only: no re-roll, no re-render)\n")
    print(report_text(report))
    return 0 if report.status == "pass" else 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    from reelline.config.credentials import ConfigError
    from reelline.pipeline.caption import CaptionError
    from reelline.pipeline.db import DbError
    from reelline.pipeline.sources import SourceRegistryError

    parser = argparse.ArgumentParser(prog="python -m reelline.pipeline.qa", description="QA checks on a rendered reel.")
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=conf().sources, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="check an existing render (report only); exit 1 if QA fails")
    p.add_argument("reel_dir", type=Path)
    p.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (QaError, ConfigError, CaptionError, DbError, SourceRegistryError) as exc:
        print(f"FAIL: {exc}")
        return 2
