"""Command line: usage text, recent/caption/card/send commands and main()."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from reelline.config import conf
from reelline.media.script_markup import (
    parse,
)
from reelline.paths import stored_path
from reelline.pipeline import db
from reelline.pipeline import script as _s
from reelline.pipeline.script.base import ScriptError, _today
from reelline.pipeline.script.commands import cmd_run, cmd_show
from reelline.pipeline.script.files import card_text, factcheck_summary, fx_text, status_mark, write_card_file
from reelline.pipeline.script.lock import display_text
from reelline.pipeline.script.runs import _update
from reelline.pipeline.script.trial import cmd_trial
from reelline.providers import TransportFailure

PICK_SLOTS = conf().channel.slots

USAGE = """Usage:
  python -m reelline.pipeline.script run [--pick ID | --slot HH:MM [--date YYYY-MM-DD]]
      Brief -> story -> punch-up -> parser check -> fact-check (one repair round) ->
      caption, for a pick (the slot's pick; the date defaults to today in Tbilisi).
      An ok or flagged script is written to content/reels/auto/<date>_<slot>_<slug>.txt
      (render_reel.py input) and its pick becomes "scripted". Exit 1 if it failed.
  python -m reelline.pipeline.script show <script_id>
      The draft, the final script, the draft-lock diff, the fact-check, the caption,
      and every call's tokens and cost.
  python -m reelline.pipeline.script recent-lines
      What {{RECENT_LINES}} is filled with now.
  python -m reelline.pipeline.script trial --brief ID [--brief ID ...] [--flirt on|off]
      A trial for prompt changes: story, punch-up and the proofread pass from a stored
      brief, plus the proofread pass on that brief's last caption hook. Nothing is stored
      (no script, no pick touched) but the calls' cost, tagged manual. Prints each
      script before and after the proofread, the fixes, why one was kept, and the cost;
      writes them to out/trials/.
  python -m reelline.pipeline.script caption <script_id>
      The assembled post caption (writes the caption fields first if missing).
  python -m reelline.pipeline.script card <script_id> [--out FILE] [--save] [--max-usd USD]
      The end card's facts for a script, made the way the chain makes them (reelline/pipeline/endcard.py:
      written, fact-checked, one repair round, failing lines left off). The calls' cost is
      recorded (manual); the lines are printed, written to --out as a facts file, and with
      --save stored on the script (scripts.passes) and written next to its file
      (<script>.endcard.json, what render_reel.py draws). --max-usd: no call starts once it
      could pass that.
  python -m reelline.pipeline.script send <script_id>
      The final script, the fact-check summary and the caption to Telegram as text
      (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); nothing is rendered.

Claude: the models.providers.anthropic.api_key_env variable, Gemini: GEMINI_API_KEY, both from .env only.
"""


def cmd_recent(args, conn, names) -> int:
    print(_s.recent_lines(conn))
    return 0


def cmd_caption(args, conn, names) -> int:
    from reelline.pipeline.caption import assemble_caption, write_caption
    from reelline.providers import get_llm

    if conn.execute("SELECT 1 FROM captions WHERE script_id = ?", (args.script_id,)).fetchone() is None:
        write_caption(conn, get_llm("writer"), args.script_id)
    print(assemble_caption(conn, args.script_id, names))
    return 0


def cmd_card(args, conn, names) -> int:
    from reelline.media.outro import load_outro, write_facts
    from reelline.pipeline.endcard import make_card, summary_lines
    from reelline.providers import get_llm

    row = conn.execute("SELECT * FROM scripts WHERE id = ?", (args.script_id,)).fetchone()
    if row is None or not row["final"]:
        raise ScriptError(f"Script #{args.script_id} has no final text")
    if args.save and not row["path"]:
        raise ScriptError(f"Script #{args.script_id} has no file to write the card next to; use --out")
    brief = conn.execute("SELECT markdown FROM briefs WHERE id = ?", (row["brief_id"],)).fetchone()
    story = db.get_story(conn, row["story_id"])
    pick = conn.execute("SELECT slot_date FROM picks WHERE id = ?", (row["pick_id"],)).fetchone()
    publish_date = date.fromisoformat(pick["slot_date"]) if pick and pick["slot_date"] else _today()
    with db.cost_context(manual=True):
        card = make_card(
            conn,
            get_llm("writer"),
            get_llm("checker"),
            brief=brief["markdown"],
            script_text=display_text(parse(row["final"])),
            source_text=story["text"],
            publish_date=publish_date,
            script_id=row["id"],
            max_usd=args.max_usd,
        )
    print(f"\nEnd card for script #{row['id']} (cost ${card.cost_usd:.4f}, manual):")
    print("\n".join([f"  {k}: {v}" for k, v in card.lines.items()] + summary_lines(card.to_json())))
    if args.out:
        print(f"Written: {write_facts(args.out, card.lines, load_outro(), script_id=row['id'], dropped=card.dropped)}")
    if args.save:
        passes = json.loads(row["passes"] or "{}")
        passes["card"] = card.to_json()
        _update(conn, row["id"], passes=json.dumps(passes, ensure_ascii=False))
        print(f"Saved on script #{row['id']}; written: {write_card_file(stored_path(row['path']), row['id'], card)}")
    return 0


def cmd_send(args, conn, names) -> int:
    from reelline.notify import get_notifier
    from reelline.pipeline.caption import assemble_caption

    row = conn.execute("SELECT * FROM scripts WHERE id = ?", (args.script_id,)).fetchone()
    if row is None or not row["final"]:
        raise ScriptError(f"Script #{args.script_id} has no final text")
    notifier = get_notifier()
    header = f"{status_mark(row['status'])}Script #{row['id']} ({row['status']})"
    for text in (
        f"{header}\n\n{row['final']}",
        f"Fact-check, script #{row['id']}\n\n{factcheck_summary(row)}"
        f"\n\nEnd card\n{card_text(row)}\n\nFact graphics\n{fx_text(row)}",
        f"Caption, script #{row['id']}\n\n{assemble_caption(conn, row['id'], names)}",
    ):
        for start in range(0, len(text), notifier.text_limit):
            notifier.send(text[start : start + notifier.text_limit])
    print(f"Sent script #{row['id']} to {notifier.name.capitalize()} (3 messages)")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from reelline.config.credentials import ConfigError
    from reelline.notify.base import NotifyError as TelegramError
    from reelline.pipeline.caption import CaptionError
    from reelline.pipeline.endcard import CardError
    from reelline.pipeline.factcheck import FactCheckError
    from reelline.pipeline.sources import SourceRegistryError, load_sources

    parser = argparse.ArgumentParser(
        prog="python -m reelline.pipeline.script",
        description="Write reel scripts.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=conf().sources, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--pick", type=int)
    p.add_argument("--slot", choices=PICK_SLOTS)
    p.add_argument("--date")
    p.set_defaults(func=cmd_run)
    for name, func in (("show", cmd_show), ("caption", cmd_caption), ("send", cmd_send)):
        p = sub.add_parser(name)
        p.add_argument("script_id", type=int)
        p.set_defaults(func=func)
    sub.add_parser("recent-lines").set_defaults(func=cmd_recent)
    p = sub.add_parser("card", help="the end card's facts for a script (costs recorded as manual)")
    p.add_argument("script_id", type=int)
    p.add_argument("--out", type=Path, help="write the facts file here")
    p.add_argument("--save", action="store_true", help="store it on the script and write it next to its file")
    p.add_argument("--max-usd", type=float, default=None, help="no call starts once it could pass this")
    p.set_defaults(func=cmd_card)
    p = sub.add_parser("trial", help="story, punch-up and proofread from stored briefs; nothing stored but costs")
    p.add_argument("--brief", type=int, action="append", required=True)
    p.add_argument("--flirt", choices=("on", "off"), default="off", help="force the flirt line on or off")
    p.set_defaults(func=cmd_trial)
    args = parser.parse_args(argv)
    try:
        conn = db.connect(args.db)
        names = {s.id: s.name for s in load_sources(args.registry)}
        return args.func(args, conn, names)
    except (
        ScriptError,
        FactCheckError,
        CaptionError,
        TelegramError,
        ConfigError,
        SourceRegistryError,
        db.DbError,
        TransportFailure,
        CardError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
