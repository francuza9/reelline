"""The pick command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ConfigError
from reelline.notify.base import NotifyError
from reelline.pipeline import db
from reelline.pipeline.assess import AssessError
from reelline.pipeline.brief import BriefError, build_brief
from reelline.pipeline.pick.assessments import cmd_assess
from reelline.pipeline.pick.base import PickError, source_names, today_tbilisi
from reelline.pipeline.pick.listing import cmd_list, cmd_queue
from reelline.pipeline.pick.picker import Picker
from reelline.pipeline.pick.supply import cmd_supply
from reelline.pipeline.sources import SourceRegistryError
from reelline.providers import LLMError, get_llm
from reelline.providers.retry import TransportFailure


def cmd_brief(args, picker: Picker) -> int:
    pick = picker.conn.execute(
        "SELECT id FROM picks WHERE story_id = ? AND status != 'rejected' ORDER BY id DESC LIMIT 1", (args.story_id,)
    ).fetchone()
    brief = build_brief(
        picker.conn,
        picker.client_factory(),
        args.story_id,
        picker.source_names,
        pick["id"] if pick else None,
        out_dir=picker.briefs_dir,
    )
    print(f"Brief: {brief.path}")
    for warning in brief.warnings:
        print(f"  check: {warning}")
    return 0


def cmd_run(args, picker: Picker) -> int:
    day = date.fromisoformat(args.date) if args.date else today_tbilisi()
    return picker.run(day, args.slot, dry_run=args.dry_run)


def cmd_choose(args, picker: Picker) -> int:
    picker.resolve(args.menu_id, args.n, "human")
    return 0


def cmd_none(args, picker: Picker) -> int:
    picker.none_of_these(args.menu_id)
    return 0


def cmd_dismiss_unselected(args, picker: Picker) -> int:
    for line in picker.dismiss_unselected(args.menu_id):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m reelline.pipeline.pick", description="Choose a story per slot.")
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=conf().sources, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("assess", help="assess stories not assessed yet")
    p.add_argument(
        "--force",
        action="store_true",
        help="re-assess (every story, or the --grep / --story ones) and report what changed",
    )
    p.add_argument(
        "--stale", action="store_true", help="re-assess the recent stories assessed under an older prompt version"
    )
    p.add_argument(
        "--grep",
        help="only stories whose title or text matches this regex, or a PICK grep_presets "
        f"name ({', '.join(conf().steps.pick.grep_presets)})",
    )
    p.add_argument("--story", type=int, action="append", help="only this story id (repeatable)")
    p.add_argument("--source", action="append", help="only this source's stories (repeatable)")
    p.set_defaults(func=cmd_assess)
    p = sub.add_parser("list", help="the ranking for the next slot")
    p.add_argument("--topic", choices=("education", "tech", "startups", "other"))
    p.add_argument("--source", action="append", help="only this source's stories (repeatable), with a count per source")
    p.add_argument("--all", action="store_true", help="also the ineligible stories and why")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("queue", help="queued picks, re-checked")
    p.set_defaults(func=cmd_queue)
    p = sub.add_parser("supply", help="the supply check: how many stories pass the rules (dry; costs recorded)")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=cmd_supply)
    p = sub.add_parser("brief", help="(re)build the fact brief for a story")
    p.add_argument("story_id", type=int)
    p.set_defaults(func=cmd_brief)
    p = sub.add_parser("run", help="pick for one slot")
    p.add_argument("--slot", required=True, choices=conf().channel.slots)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in Tbilisi)")
    p.add_argument("--dry-run", action="store_true", help="show what would happen; no Gemini, Telegram or writes")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("choose", help="answer a menu from the command line")
    p.add_argument("menu_id", type=int)
    p.add_argument("n", type=int, nargs="+", help="menu numbers, in order of preference")
    p.set_defaults(func=cmd_choose)
    p = sub.add_parser("none", help="answer a menu with None of these")
    p.add_argument("menu_id", type=int)
    p.set_defaults(func=cmd_none)
    p = sub.add_parser("dismiss-unselected", help="backfill: dismiss an answered menu's unchosen contenders")
    p.add_argument("menu_id", type=int)
    p.set_defaults(func=cmd_dismiss_unselected)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable = lambda: get_llm("assessor"),
    channel_factory: Callable[[], object] | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = db.connect(args.db)
        kwargs = {"source_names": source_names(args.registry), "client_factory": client_factory}
        if channel_factory is not None:
            kwargs["channel_factory"] = channel_factory
        if now is not None:
            kwargs["now"] = now
        picker = Picker(conn, **kwargs)
        return args.func(args, picker)
    except (
        PickError,
        AssessError,
        BriefError,
        LLMError,
        NotifyError,
        ConfigError,
        SourceRegistryError,
        db.DbError,
        TransportFailure,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
