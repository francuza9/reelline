"""Effects (the tick's side effects; tests pass fakes), process helpers and once()."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from reelline.config import conf
from reelline.paths import REPO_ROOT
from reelline.pipeline import db
from reelline.pipeline.run_daily.base import utcnow
from reelline.pipeline.scoring import tbilisi


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    stat = Path(f"/proc/{pid}/stat")
    try:
        if stat.exists():
            return not stat.read_text().split(") ", 1)[1].startswith("Z")
        os.kill(pid, 0)
    except (OSError, IndexError):
        return False
    return True


def bot_pids() -> list[int]:
    """Running `python -m reelline.pipeline.approve_bot` listeners (not its job workers)."""
    out = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"reelline.pipeline.approve_bot" in cmd and b"job" not in cmd and pid_alive(int(proc.name)):
            out.append(int(proc.name))
    return out


def log_path(name: str, now: datetime | None = None) -> Path:
    day = (now or utcnow()).astimezone(tbilisi()).date().isoformat()
    path = conf().storage.log_dir / f"{name}-{day}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def spawn_detached(args: list[str], log_name: str) -> subprocess.Popen:
    """`python -m ...` in its own session (it outlives the tick), output to logs/."""
    with log_path(log_name).open("ab") as out:
        return subprocess.Popen(
            [sys.executable, "-m", *args],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _stats_tick(conn, now, say, log):
    from reelline.pipeline import stats

    return stats.tick(conn, now, say, log)


def _deliver_review(conn, reel_id):
    from reelline.pipeline.produce import deliver_review

    return deliver_review(conn, reel_id)


@dataclass
class Effects:
    now: Callable[[], datetime] = utcnow
    send: Callable[[str, list | None], object] | None = None  # the Notifier (text, buttons)
    tg: object | None = None  # the review channel, for approve_bot.report_failure()
    spawn_job: Callable[[int], object] | None = None
    job_alive: Callable[[int | None, int], bool] | None = None
    spawn_ingest: Callable[[int], object] = lambda task_id: spawn_detached(
        ["reelline.pipeline.run_daily", "ingest-worker", str(task_id)], "ingest"
    )
    pid_alive: Callable[[int | None], bool] = pid_alive
    start_bot: Callable[[], object] = lambda: spawn_detached(["reelline.pipeline.approve_bot"], "bot")
    bot_running: Callable[[], bool] = lambda: bool(bot_pids())
    supervisor: Callable[[], str] = lambda: conf().steps.run_daily.bot_supervisor  # "tick" | "systemd"
    # reelline/pipeline/stats.py tick(conn, now, say, log): the due stats snapshots.
    stats: Callable = lambda conn, now, say, log: _stats_tick(conn, now, say, log)
    # A held review sent (reelline/pipeline/produce.py deliver_review(conn, reel_id)).
    deliver_review: Callable = lambda conn, reel_id: _deliver_review(conn, reel_id)
    # Kill a hung worker's process group (approve_bot.kill_worker()).
    kill_job: Callable[[int], None] | None = None
    log: Callable[[str], None] = lambda m: print(m, flush=True)
    actions: list[str] = field(default_factory=list)  # what this tick did (tests, the log)

    def __post_init__(self):
        from reelline.pipeline import approve_bot as ab

        self.spawn_job = self.spawn_job or ab.spawn_process
        self.job_alive = self.job_alive or ab.process_alive
        self.kill_job = self.kill_job or ab.kill_worker

    def did(self, text: str) -> None:
        self.actions.append(text)
        self.log(text)

    def _telegram(self) -> None:
        """The alerts' Notifier (notify.channel; a raw client in `tg` is its connection) and the
        review channel for failure reports (their Retry taps come back to the bot)."""
        if self.send is None:
            from reelline.notify import get_notifier
            from reelline.review import get_review_channel

            notifier = get_notifier(self.tg)
            self.tg = get_review_channel(self.tg)
            self.send = lambda t, m: notifier.send(t, m)

    def say(self, text: str, markup: list | None = None) -> None:
        self._telegram()
        try:
            self.send(text, markup)
        except Exception as exc:  # Telegram down: the log still has it
            self.log(f"send failed: {exc}")

    def reaped(self, conn: sqlite3.Connection, job: sqlite3.Row, text: str) -> None:
        """A hung job killed (approve_bot.notify_reaped())."""
        from reelline.pipeline import approve_bot as ab

        self._telegram()
        try:
            ab.notify_reaped(conn, self.tg, self.say, job, text, log=self.log)
        except Exception as exc:
            self.log(f"report failed: {exc}")


def once(conn: sqlite3.Connection, kind: str, key: str, now: datetime, detail: str | None = None) -> bool:
    """True the first time (kind, key) is seen: alerts are said once."""
    with db.transaction(conn):
        return (
            conn.execute(
                "INSERT OR IGNORE INTO daily_events (kind, key, at, detail) VALUES (?, ?, ?, ?)",
                (kind, key, db.now_iso(now), detail),
            ).rowcount
            == 1
        )
