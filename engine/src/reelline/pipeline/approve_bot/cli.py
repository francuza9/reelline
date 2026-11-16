"""Command line: the listener, or one worker job."""

from __future__ import annotations

import signal
from datetime import datetime
from pathlib import Path

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.approve_bot.bot import ApproveBot
from reelline.pipeline.approve_bot.worker import Steps, run_job


def main(argv: list[str] | None = None) -> int:
    import argparse

    from reelline.pipeline import approve_bot
    from reelline.pipeline.sources import load_sources
    from reelline.review import get_review_channel

    parser = argparse.ArgumentParser(
        prog="python -m reelline.pipeline.approve_bot", description=(approve_bot.__doc__ or "").splitlines()[0]
    )
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, default=conf().sources, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("job", help="run one worker job (the bot starts these)")
    p.add_argument("job_id", type=int)
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    names = {s.id: s.name for s in load_sources(args.registry)}
    tg = get_review_channel()

    def log(m):
        return print(f"{datetime.now().strftime('%H:%M:%S')} {m}", flush=True)

    if args.command == "job":
        return 0 if run_job(conn, args.job_id, tg, Steps(conn, names, log), log) == "done" else 1
    bot = ApproveBot(conn, tg, source_names=names, log=log)

    def stop(signum, frame):
        bot.stopping = True
        log("Stopping after this poll...")

    signal.signal(signal.SIGTERM, stop)
    try:
        bot.run()
    except KeyboardInterrupt:
        log("Approval bot stopped (Ctrl-C)")
    return 0
