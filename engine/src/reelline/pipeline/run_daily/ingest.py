"""The ingest task."""

from __future__ import annotations

import sqlite3
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.run_daily.base import DIED
from reelline.pipeline.run_daily.effects import Effects, once


def ingest_state(conn: sqlite3.Connection, fx: Effects, now: datetime) -> str:
    """running | fresh (one finished within ingest_skip_if_ran_min) | stale. A task whose
    process is gone is marked failed (said once)."""
    for task in conn.execute("SELECT * FROM daily_tasks WHERE kind = 'ingest' AND status = 'running'").fetchall():
        if fx.pid_alive(task["pid"]):
            return "running"
        with db.transaction(conn):
            conn.execute(
                "UPDATE daily_tasks SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                (DIED, db.now_iso(now), task["id"]),
            )
        if once(conn, "ingest_failed", str(task["id"]), now):
            fx.say(f"⚠️ Ingest (task #{task['id']}) died; the next one runs on schedule.")
    last = conn.execute("SELECT MAX(finished_at) FROM runs WHERE finished_at IS NOT NULL").fetchone()[0]
    if last and now - datetime.fromisoformat(last) < timedelta(minutes=conf().steps.run_daily.ingest_skip_if_ran_min):
        return "fresh"
    return "stale"


def start_ingest(conn: sqlite3.Connection, fx: Effects, now: datetime) -> int:
    with db.transaction(conn):
        task_id = conn.execute(
            "INSERT INTO daily_tasks (kind, status, started_at) VALUES ('ingest', 'running', ?)", (db.now_iso(now),)
        ).lastrowid
    child = fx.spawn_ingest(task_id)
    with db.transaction(conn):
        conn.execute("UPDATE daily_tasks SET pid = ? WHERE id = ?", (getattr(child, "pid", None), task_id))
    fx.did(f"ingest task #{task_id} started")
    return task_id


def ingest_due(conn: sqlite3.Connection, now: datetime) -> bool:
    last = conn.execute("SELECT MAX(started_at) FROM daily_tasks WHERE kind = 'ingest'").fetchone()[0]
    return last is None or now - datetime.fromisoformat(last) >= timedelta(hours=conf().steps.run_daily.ingest_every_h)


def ingest_worker(conn: sqlite3.Connection, task_id: int, run: Callable[[], int] | None = None) -> int:
    """The detached ingest (python -m reelline.pipeline.ingest's run); its task row says how it ended."""

    def default_run() -> int:
        from reelline.pipeline import ingest

        return ingest.main([])

    try:
        code = (run or default_run)()
        status, error = ("done", None) if code == 0 else ("failed", f"ingest exited {code} (every source failed)")
    except Exception as exc:
        traceback.print_exc()
        status, error, code = "failed", f"{type(exc).__name__}: {exc}"[:800], 1
    with db.transaction(conn):
        conn.execute(
            "UPDATE daily_tasks SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, db.now_iso(), task_id),
        )
    if status == "failed":
        Effects().say(f"⚠️ Ingest failed (task #{task_id}): {error}")
    return code
