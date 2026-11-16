"""What the bot says about a job: failure, recovery, a reopened review."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime

from reelline.config import conf
from reelline.notify.base import NotifyError, buttons
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline.approve_bot.base import (
    CHAIN_KINDS,
    JOB_LABELS,
    hm,
)
from reelline.pipeline.approve_bot.jobs import (
    update_job,
)
from reelline.review import machine as review


def job_reel(conn: sqlite3.Connection, job: sqlite3.Row) -> int | None:
    """The reel a job is about: a publish job's, or its review's; None for a job
    before any reel (a pick, a first script and reel)."""
    detail = json.loads(job["detail"] or "{}")
    if detail.get("reel_id"):
        return detail["reel_id"]
    r = review.get(conn, job["review_id"]) if job["review_id"] else None
    return r["reel_id"] if r else None


def failure_text(conn: sqlite3.Connection, job: sqlite3.Row) -> str:
    text = (
        f"❌ Job #{job['id']} ({JOB_LABELS[job['kind']]}, {job['slot_date']} {job['slot_time']}) failed at "
        f"step {job['step'] or 'start'}: {job['error']}"
    )
    reel_id = job_reel(conn, job)
    return text if reel_id is None else about_reel(reel_id, text)


def report_failure(conn: sqlite3.Connection, tg, job: sqlite3.Row, log: Callable[[str], None] = print) -> None:
    """The failure in the review channel with a Retry button; the job's review gets its buttons back."""
    text = failure_text(conn, job)
    tg = review.channel(tg)
    try:
        ref = tg.send(text, buttons([[(conf().review.approve.labels["retry"], f"j:{job['id']}:rt")]]))
        update_job(conn, job["id"], message_id=ref)
    except NotifyError as exc:
        log(f"job #{job['id']}: failure report not sent: {exc}")
    r = review.get(conn, job["review_id"]) if job["review_id"] else None
    if r is not None and r["status"] == "pending" and r["state"] == "working":
        review.refresh(
            conn,
            tg,
            r["id"],
            state="main",
            log=log,
            line=f"⚠️ job #{job['id']} failed (see below); this reel is still up for review",
        )


def report_recovery(conn: sqlite3.Connection, tg, job: sqlite3.Row, log: Callable[[str], None] = print) -> None:
    """A job that failed before (it has a failure report: report_failure() set its
    message_id) and has now finished: a ✅ reply to that report, a new message so it
    notifies (an edit doesn't). A first-time success sends nothing: its review or menu
    is the news."""
    if job is None or not job["message_id"]:
        return
    text = (
        f"✅ Job #{job['id']} ({JOB_LABELS[job['kind']]}, {job['slot_date']} {job['slot_time']}) succeeded "
        f"on retry (attempt {job['attempts']}): {job['result'] or 'done'}"
    )
    reel_id = job_reel(conn, job)
    text = text if reel_id is None else about_reel(reel_id, text)
    try:
        review.channel(tg).send(text[: conf().review.approve.job_error_chars], reply_to=job["message_id"])
    except NotifyError as exc:
        log(f"job #{job['id']}: recovery report not sent: {exc}")


def reopen_review(
    conn: sqlite3.Connection, tg, review_id: int, by: str, now: datetime, log: Callable[[str], None] = print
) -> None:
    """An unscheduled reel's review back to pending with its buttons (its 24 h start over)."""
    review.refresh(
        conn,
        tg,
        review_id,
        log=log,
        now=now,
        status="pending",
        state="main",
        decided_at=None,
        decided_by=None,
        sent_at=db.now_iso(now),
        reminded_at=None,
        line=f"↩️ unscheduled by {by} {hm(now)}: back to review",
    )


def notify_reaped(
    conn: sqlite3.Connection,
    tg,
    say: Callable[[str], object],
    job: sqlite3.Row,
    text: str,
    log: Callable[[str], None] = print,
) -> None:
    """A reaped job said in the chat; one the tick doesn't resume also gets its failure
    report (Retry, and its review's buttons back)."""
    say(text)
    if job["kind"] not in CHAIN_KINDS:
        report_failure(conn, tg, job, log=log)


def pick_line(conn: sqlite3.Connection, pick: sqlite3.Row, rest: str) -> str:
    """ "⏳ Pick #5 · <title> · 2026-10-04 13:00: <rest>": the title, since "Pick #5" right
    after a numbered menu reads like the menu's #5."""
    story = db.get_story(conn, pick["story_id"])
    return f"⏳ Pick #{pick['id']} · {story['title'][:80]} · {pick['slot_date']} {pick['slot_time']}: {rest}"
