"""The produce command line."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from reelline.config import conf
from reelline.media.backgrounds import (
    BackgroundError,
)
from reelline.pipeline import db
from reelline.pipeline import produce as _p
from reelline.pipeline.produce.backgrounds import backgrounds_report
from reelline.pipeline.produce.base import ProduceError
from reelline.pipeline.produce.report import reel_report, status_block
from reelline.pipeline.produce.review import send_review_messages


def cmd_backgrounds(args, conn) -> int:
    print("\n".join(backgrounds_report(conn)))
    return 0


def cmd_run(args, conn) -> int:
    from reelline.pipeline.scoring import tbilisi
    from reelline.pipeline.sources import load_sources

    day = args.date or datetime.now(UTC).astimezone(tbilisi()).date().isoformat()
    names = {s.id: s.name for s in load_sources(args.registry)}
    result = _p.produce(
        conn, day, args.slot, names, background=args.background, send=not args.no_send, qa=not args.no_qa
    )
    if result.status != "rendered":
        print(f"FAILED: reel #{result.reel_id} {result.status}")
        return 1
    print("\n" + reel_report(conn, result.reel_id))
    return 0


def cmd_review(args, conn) -> int:
    reel = conn.execute("SELECT * FROM reels WHERE id = ?", (args.reel_id,)).fetchone()
    if reel is None:
        raise ProduceError(f"No reel #{args.reel_id}")
    if reel["status"] != "rendered":
        raise ProduceError(f"Reel #{reel['id']} is {reel['status']}; only a rendered reel can be reviewed")
    from reelline.review import machine as review

    old = review.for_reel(conn, reel["id"])
    if old is not None and old["status"] == "pending":
        raise ProduceError(f"Reel #{reel['id']} already has pending review #{old['id']}")
    bot = _p.Review()
    if args.video:
        review_id = send_review_messages(conn, reel["id"], bot)
    else:
        bot.send_text(review.header_text(conn, reel["id"]))
        bot.send_text(reel["caption"] or "(no caption)")
        review_id = bot.send_review(conn, reel["id"], status_block(conn, reel))
    print(f"Review #{review_id} for reel #{reel['id']} sent to Telegram")
    return 0


def cmd_show(args, conn) -> int:
    print(reel_report(conn, args.reel_id))
    return 0


USAGE = """\
Usage:
  python -m reelline.pipeline.produce run --slot HH:MM [--date YYYY-MM-DD] [--background NAME] [--no-send] [--no-qa]
      Render the slot's scripted pick (date: today in Tbilisi) with the rotation's
      background (or NAME), store the reel, run QA (reelline/pipeline/qa.py; --no-qa skips
      it, loudly), send the review to Telegram. Exit 1 if the render failed twice,
      or a render during QA failed (an alert is sent). A reel that fails QA still
      goes to review, marked QA ❌.
  python -m reelline.pipeline.produce show <reel_id>
      The reel's files, caption and status block.
  python -m reelline.pipeline.produce review <reel_id> [--video]
      Send a rendered reel's caption and review (status block with the buttons) again,
      e.g. for a reel rendered before the approval bot; not when it has a pending one.
      --video: the whole review as the worker sends it (header, video and cover,
      caption, status block), e.g. after a review send that failed.
  python -m reelline.pipeline.produce backgrounds
      The enabled and disabled backgrounds (sidecar overrides, problems), the last
      one used and the rotation's next.
"""


def main(argv: list[str] | None = None) -> int:
    import argparse

    from reelline.core.errors import ConfigError
    from reelline.notify.base import NotifyError as TelegramError
    from reelline.pipeline.caption import CaptionError
    from reelline.pipeline.sources import SourceRegistryError

    parser = argparse.ArgumentParser(
        prog="python -m reelline.pipeline.produce",
        description="Render reels for review.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=conf().sources, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--slot", required=True, choices=conf().channel.slots)
    p.add_argument("--date")
    p.add_argument("--background")
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--no-qa", action="store_true")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("show")
    p.add_argument("reel_id", type=int)
    p.set_defaults(func=cmd_show)
    p = sub.add_parser("review")
    p.add_argument("reel_id", type=int)
    p.add_argument("--video", action="store_true")
    p.set_defaults(func=cmd_review)
    sub.add_parser("backgrounds").set_defaults(func=cmd_backgrounds)
    args = parser.parse_args(argv)
    try:
        conn = db.connect(args.db)
        return args.func(args, conn)
    except (
        ProduceError,
        BackgroundError,
        db.DbError,
        CaptionError,
        TelegramError,
        ConfigError,
        SourceRegistryError,
    ) as exc:
        print(f"FAIL: {exc}")
        return 1
