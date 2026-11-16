"""The tick."""

from __future__ import annotations

import sqlite3
import traceback
from datetime import datetime

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.run_daily.advance import (
    advance,
)
from reelline.pipeline.run_daily.alerts import late_alerts, supervise_bot, thinking_alerts, waited_alert
from reelline.pipeline.run_daily.effects import Effects, once
from reelline.pipeline.run_daily.evening import deliver
from reelline.pipeline.run_daily.ingest import ingest_due, ingest_state, start_ingest
from reelline.pipeline.run_daily.slots import (
    candidate_slots,
    slot_dt,
)


def prune_logs(now: datetime) -> None:
    cutoff = now.timestamp() - conf().steps.run_daily.log_keep_days * 86400
    for path in conf().storage.log_dir.glob("*.log"):
        if path.stat().st_mtime < cutoff:
            path.unlink()


def tick(conn: sqlite3.Connection, fx: Effects) -> list[str]:
    """One tick (the module docstring). Returns what it did."""
    from reelline.pipeline import approve_bot as ab

    now = fx.now()
    if db.get_state(conn, "paused"):
        fx.did(f"paused since {db.get_state(conn, 'paused')}: nothing new started")
        run_steps(
            conn,
            fx,
            now,
            [
                (
                    "reap",
                    lambda: ab.reap_overdue(
                        conn, now, fx.job_alive, fx.kill_job, lambda job, text: fx.reaped(conn, job, text), fx.log
                    ),
                ),
                ("deliver", lambda: deliver(conn, fx, now, summary=False)),
                ("stats", lambda: stats_step(conn, fx, now)),
                ("retention", lambda: retention_step(conn, fx, now)),
                ("thinking", lambda: thinking_alerts(conn, fx, now)),
            ],
        )
        return fx.actions
    steps = [
        (
            "reap",
            lambda: ab.reap_overdue(
                conn, now, fx.job_alive, fx.kill_job, lambda job, text: fx.reaped(conn, job, text), fx.log
            ),
        ),
        ("bot", lambda: supervise_bot(conn, fx, now)),
        ("deliver", lambda: deliver(conn, fx, now)),
        (
            "ingest",
            lambda: ingest_due(conn, now) and ingest_state(conn, fx, now) == "stale" and start_ingest(conn, fx, now),
        ),
    ]
    slots = sorted(candidate_slots(now), key=lambda s: slot_dt(*s))
    steps += [(f"chain {d} {t}", lambda d=d, t=t: one_at_a_time(conn, d, t, slots, fx)) for d, t in slots]
    steps += [
        ("late alerts", lambda: late_alerts(conn, fx, now)),
        ("thinking", lambda: thinking_alerts(conn, fx, now)),
        (
            "dispatch",
            lambda: ab.dispatch_queued(
                conn,
                fx.spawn_job,
                fx.job_alive,
                fx.log,
                lambda job, error: ab.update_job(
                    conn, job["id"], status="failed", error=error, finished_at=db.now_iso(now)
                ),
            ),
        ),
        ("stats", lambda: stats_step(conn, fx, now)),
        ("retention", lambda: retention_step(conn, fx, now)),
        ("logs", lambda: prune_logs(now)),
    ]
    run_steps(conn, fx, now, steps)
    return fx.actions


# Jobs that don't load the server (no render): they never hold another chain back.
LIGHT_JOBS = ("publish", "unschedule")


def one_at_a_time(
    conn: sqlite3.Connection, slot_date: str, slot_time: str, slots: list[tuple[str, str]], fx: Effects
) -> str:
    """advance(), unless another slot's chain job is running and this slot has none:
    then it waits (one render at a time)."""
    from reelline.pipeline import approve_bot as ab

    def heavy(d: str, t: str) -> bool:
        job = ab.active_job(conn, d, t)
        return job is not None and job["kind"] not in LIGHT_JOBS

    if not heavy(slot_date, slot_time):
        busy = next(((d, t) for d, t in slots if (d, t) != (slot_date, slot_time) and heavy(d, t)), None)
        if busy is not None:
            fx.did(f"{slot_date} {slot_time}: waits for {busy[0]} {busy[1]} (one chain at a time)")
            waited_alert(conn, slot_date, slot_time, busy, fx)
            return "waits"
    return advance(conn, slot_date, slot_time, fx)


def stats_step(conn: sqlite3.Connection, fx: Effects, now: datetime) -> None:
    """The due stats snapshots (reelline/pipeline/stats.py; read-only, cheap, inline)."""
    done = fx.stats(conn, now, lambda text: fx.say(text), fx.log)
    if done:
        fx.did(done)


def retention_step(conn: sqlite3.Connection, fx: Effects, now: datetime) -> None:
    """A reel's retention line after its +72 h snapshot, and the weekly hook examples file
    (reelline/pipeline/retention.py; read-only, inline)."""
    from reelline.pipeline import retention

    done = retention.tick(conn, now, lambda text: fx.say(text), fx.log)
    if done:
        fx.did(done)


def run_steps(conn: sqlite3.Connection, fx: Effects, now: datetime, steps: list) -> None:
    for name, run in steps:
        try:
            run()
        except Exception as exc:  # one broken part never stops the others
            fx.log(f"{name} failed:\n{traceback.format_exc()}")
            if once(conn, "tick_error", f"{name}:{type(exc).__name__}:{now.date()}", now):
                fx.say(f"⚠️ Daily tick: {name} failed: {type(exc).__name__}: {exc}"[:600])
