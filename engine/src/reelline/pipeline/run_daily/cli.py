"""Command line and the crontab lines."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from reelline.config import conf
from reelline.paths import REPO_ROOT
from reelline.pipeline import db
from reelline.pipeline.run_daily.advance import (
    advance,
)
from reelline.pipeline.run_daily.effects import Effects
from reelline.pipeline.run_daily.evening import evening_text
from reelline.pipeline.run_daily.ingest import ingest_worker
from reelline.pipeline.run_daily.tick import (
    tick,
)
from reelline.pipeline.scoring import tbilisi


def cron_lines(node_bin: str | None = None) -> str:
    npx = node_bin or (str(Path(shutil.which("npx")).parent) if shutil.which("npx") else None)
    if npx is None:
        raise SystemExit(
            "FAIL: npx not found on PATH (Remotion and wrangler need node): open a shell where "
            "`npx --version` works and run install-cron there"
        )
    python = Path(sys.prefix) / "bin" / "python"
    from reelline.config.credentials import resolve_env_file

    logs = conf().storage.log_dir
    every = conf().steps.run_daily.tick_every_min
    return "\n".join(
        [
            "# Daily schedule (reelline/pipeline/run_daily). Times live in the channel's config.yaml (steps.run_daily), not here.",
            "SHELL=/bin/bash",
            f"PATH={npx}:/usr/local/bin:/usr/bin:/bin",
            "LANG=C.UTF-8",
            f"*/{every} * * * * cd {REPO_ROOT} && REELLINE__STORAGE__ENV_FILE={resolve_env_file()} {python} -m reelline.pipeline.run_daily tick "
            f">> {logs}/tick-$(TZ={conf().channel.timezone} date +\\%Y-\\%m-\\%d).log 2>&1",
        ]
    )


# ---- CLI ------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from reelline.pipeline import run_daily

    parser = argparse.ArgumentParser(
        prog="python -m reelline.pipeline.run_daily", description=(run_daily.__doc__ or "").splitlines()[0]
    )
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tick", help="what cron calls")
    p = sub.add_parser("slot", help="start or move on one slot's chain now")
    p.add_argument("slot_time", choices=conf().channel.slots)
    p.add_argument("--date", help="YYYY-MM-DD (default: today, Tbilisi)")
    sub.add_parser("status", help="the bot's /status")
    sub.add_parser("pause", help="the bot's /pause (said in the chat)")
    sub.add_parser("resume", help="the bot's /resume (said in the chat)")
    p = sub.add_parser("evening", help="send the evening summary now")
    p.add_argument("--print", action="store_true", help="only print it")
    sub.add_parser("install-cron", help="print the crontab lines (writes nothing)")
    p = sub.add_parser("ingest-worker", help=argparse.SUPPRESS)
    p.add_argument("task_id", type=int)
    args = parser.parse_args(argv)
    if args.command == "install-cron":
        print(cron_lines())
        return 0
    conn = db.connect(args.db)
    fx = Effects(log=lambda m: print(f"{datetime.now().strftime('%H:%M:%S')} {m}", flush=True))
    now = fx.now()
    if args.command == "tick":
        tick(conn, fx)
        if not fx.actions:
            fx.log("tick: nothing to do")
        return 0
    if args.command in ("pause", "resume"):
        from reelline.pipeline import approve_bot as ab

        pausing = args.command == "pause"
        if bool(db.get_state(conn, "paused")) == pausing:
            print(f"Already {'paused' if pausing else 'running'} (paused: {db.get_state(conn, 'paused') or 'no'})")
            return 0
        db.set_state(conn, "paused", db.now_iso(now) if pausing else None)
        text = (ab.PAUSED_TEXT if pausing else ab.RESUMED_TEXT) + f" (make {args.command})"
        fx.say(text)
        print(text)
        return 0
    if args.command == "slot":
        if db.get_state(conn, "paused"):
            print("Paused (/resume in the bot first)")
            return 1
        day = args.date or now.astimezone(tbilisi()).date().isoformat()
        print(f"{day} {args.slot_time}: {advance(conn, day, args.slot_time, fx, force=True)}")
        from reelline.pipeline import approve_bot as ab

        ab.dispatch_queued(
            conn,
            fx.spawn_job,
            fx.job_alive,
            fx.log,
            lambda job, error: ab.update_job(conn, job["id"], status="failed", error=error),
        )
        return 0
    if args.command == "status":
        from reelline.pipeline.approve_bot import status_text

        print(status_text(conn, now))
        return 0
    if args.command == "evening":
        text = evening_text(conn, now)
        print(text)
        if not args.print:
            fx.say(text)
        return 0
    return ingest_worker(conn, args.task_id)
