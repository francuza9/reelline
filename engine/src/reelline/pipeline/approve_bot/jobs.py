"""The jobs table (shared by the bot and the worker): rows, dispatch, reaping, spawning."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from reelline.config import conf
from reelline.paths import REPO_ROOT
from reelline.pipeline import db
from reelline.pipeline.approve_bot.base import (
    CHAIN_KINDS,
    JOB_KINDS,
    JOB_LABELS,
    WORKER_DIED,
)


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def update_job(conn: sqlite3.Connection, job_id: int, **fields) -> None:
    if "status" in fields and fields["status"] not in db.JOB_STATUSES:
        raise db.DbError(f"Unknown job status {fields['status']!r}")
    with db.transaction(conn):
        conn.execute(
            f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), job_id)
        )


def active_job(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs WHERE slot_date = ? AND slot_time = ? AND status IN ('queued', 'running') "
        "ORDER BY id LIMIT 1",
        (slot_date, slot_time),
    ).fetchone()


def publish_job(conn: sqlite3.Connection, reel_id: int) -> sqlite3.Row | None:
    """The reel's queued or running publish job, if any."""
    for job in conn.execute(
        "SELECT * FROM jobs WHERE kind = 'publish' AND status IN ('queued', 'running') ORDER BY id"
    ).fetchall():
        if json.loads(job["detail"] or "{}").get("reel_id") == reel_id:
            return job
    return None


def add_job(
    conn: sqlite3.Connection,
    kind: str,
    pick: sqlite3.Row | dict,
    review_id: int | None = None,
    story_fate: str | None = None,
    exclusive: bool = True,
    detail: dict | None = None,
) -> int | None:
    """Insert a queued job for the pick's slot; None when `exclusive` and the slot
    already has an active job. detail: the job's arguments (publish: the reel). A
    "pick" job has no pick yet: `pick` is then {"slot_date", "slot_time", "id": None}."""
    if kind not in JOB_KINDS:
        raise db.DbError(f"Unknown job kind {kind!r}")
    with db.transaction(conn):
        if exclusive and active_job(conn, pick["slot_date"], pick["slot_time"]) is not None:
            return None
        return conn.execute(
            """INSERT INTO jobs (kind, slot_date, slot_time, pick_id, review_id, story_fate, status, created_at, detail)
               VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
            (
                kind,
                pick["slot_date"],
                pick["slot_time"],
                pick["id"],
                review_id,
                story_fate,
                db.now_iso(),
                json.dumps(detail) if detail else None,
            ),
        ).lastrowid


def dispatch_queued(
    conn: sqlite3.Connection,
    spawn: Callable[[int], object],
    alive: Callable[[int | None, int], bool],
    log: Callable[[str], None],
    on_dead: Callable[[sqlite3.Row, str], None],
    children: dict | None = None,
) -> None:
    """Start queued jobs whose slot has nothing running (the bot and the daily tick
    both call this). A worker claims its job atomically (run_job()), and the launcher
    records the pid only if none is set, so two launchers never run a job twice."""
    for job in conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id").fetchall():
        if job["pid"] and alive(job["pid"], job["id"]):
            continue  # started, not running yet
        if job["pid"]:  # started and gone before it ran
            on_dead(job, "the worker process exited before it started")
            continue
        running = conn.execute(
            "SELECT 1 FROM jobs WHERE slot_date = ? AND slot_time = ? AND status = 'running'",
            (job["slot_date"], job["slot_time"]),
        ).fetchone()
        if running:
            continue
        child = spawn(job["id"])
        pid = getattr(child, "pid", None)
        if children is not None:
            children[job["id"]] = child
        with db.transaction(conn):
            conn.execute("UPDATE jobs SET pid = ? WHERE id = ? AND pid IS NULL AND status = 'queued'", (pid, job["id"]))
        log(f"Job #{job['id']} ({job['kind']}) started, pid {pid}")


def process_alive(pid: int | None, job_id: int) -> bool:
    """Is `pid` running this job's worker (not a zombie, not a reused pid)?"""
    if not pid:
        return False
    proc = Path(f"/proc/{pid}")
    if proc.exists():
        try:
            if (proc / "stat").read_text().split(") ", 1)[1].startswith("Z"):
                return False
            cmd = (proc / "cmdline").read_bytes().split(b"\0")
            return b"approve_bot" in b" ".join(cmd) and str(job_id).encode() in cmd
        except (OSError, IndexError):
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def step_limit_min(step: str | None) -> int:
    return conf().steps.run_daily.step_max_min.get(step or "", conf().steps.run_daily.step_max_default_min)


def overdue(job: sqlite3.Row, now: datetime) -> int | None:
    """Minutes a running job's step has run (jobs.step_at, else its start) when that's
    past its limit (step_limit_min()), else None."""
    since = job["step_at"] or job["started_at"]
    if job["status"] != "running" or not since:
        return None
    ran = (now - datetime.fromisoformat(since)).total_seconds() / 60
    return int(ran) if ran > step_limit_min(job["step"]) else None


def kill_worker(pid: int, grace_s: float = 5.0) -> None:
    """SIGTERM a worker's process group (its own session: the render's node and ffmpeg
    too), SIGKILL if it's still there after grace_s."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not _running(pid):
            return
        time.sleep(0.2)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def _running(pid: int) -> bool:
    """The process exists and isn't a zombie."""
    try:
        return not Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].startswith("Z")
    except (OSError, IndexError):
        return False


def reap_overdue(
    conn: sqlite3.Connection,
    now: datetime,
    alive: Callable[[int | None, int], bool],
    kill: Callable[[int], None],
    notify: Callable[[sqlite3.Row, str], None],
    log: Callable[[str], None] = print,
) -> list[int]:
    """Running jobs whose step ran past its limit: the worker (when it's still this job's)
    is killed and the job fails as a dead worker, so it holds no other slot back and the
    tick resumes its chain. notify(job, alert) is called once per job, by whichever of the
    bot and the tick marks it failed. The ids reaped."""
    reaped = []
    for job in conn.execute("SELECT * FROM jobs WHERE status = 'running' ORDER BY id").fetchall():
        ran = overdue(job, now)
        if ran is None:
            continue
        if alive(job["pid"], job["id"]):
            kill(job["pid"])
        limit, step = step_limit_min(job["step"]), job["step"] or "start"
        error = f"{WORKER_DIED}: step {step} ran {ran} min (limit {limit}), killed"
        with db.transaction(conn):
            flipped = conn.execute(
                "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ? AND status = 'running'",
                (error, db.now_iso(now), job["id"]),
            ).rowcount
        if not flipped:
            continue
        log(f"Job #{job['id']} ({job['kind']}): {error}")
        then = "the tick resumes the chain" if job["kind"] in CHAIN_KINDS else "its failure report below has Retry"
        notify(
            get_job(conn, job["id"]),
            f"⏱ Job #{job['id']} ({JOB_LABELS[job['kind']]}, {job['slot_date']} {job['slot_time']}) hung: step "
            f"{step} ran {ran} min, over its {limit} min limit. Killed; {then}.",
        )
        reaped.append(job["id"])
    return reaped


def spawn_process(job_id: int) -> subprocess.Popen:
    """The worker for a job: a detached `python -m reelline.pipeline.approve_bot job <id>` in its
    own session, logging to storage.jobs_log_dir/job_<id>.log."""
    log_dir = Path(conf().storage.jobs_log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"job_{job_id}.log").open("ab") as out:
        return subprocess.Popen(
            [sys.executable, "-m", "reelline.pipeline.approve_bot", "job", str(job_id)],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
