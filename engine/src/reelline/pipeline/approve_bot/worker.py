"""The worker: one job to its end."""

from __future__ import annotations

import json
import os
import sqlite3
import traceback
from collections.abc import Callable

from reelline.config import conf
from reelline.media import renderlock
from reelline.notify.base import NotifyError
from reelline.pipeline import db
from reelline.pipeline.approve_bot.base import (
    ACTIVE,
    CHAIN_KINDS,
    JOB_LABELS,
    JobError,
    utcnow,
)
from reelline.pipeline.approve_bot.jobs import (
    get_job,
    update_job,
)
from reelline.pipeline.approve_bot.reporting import (
    pick_line,
    reopen_review,
    report_failure,
    report_recovery,
)
from reelline.providers.retry import TransportFailure
from reelline.review import machine as review


class Steps:
    """The real worker steps (tests pass fakes)."""

    def __init__(self, conn: sqlite3.Connection, source_names: dict[str, str], log: Callable[[str], None]):
        self.conn, self.names, self.log = conn, source_names, log

    def script(self, pick_id: int) -> int:
        """The pick's script, caption and files: the chain's brief ... save (reelline/pipeline/slices.py;
        going on with an unfinished script: reelline/pipeline/script.py write_script())."""
        from reelline.pipeline.script import ScriptError
        from reelline.pipeline.slices import run_script

        try:
            return run_script(self.conn, pick_id, source_names=self.names, log=self.log)
        except ScriptError as exc:
            raise JobError(str(exc)) from None

    def publish_clients(self, reel_id: int):
        from reelline.pipeline import publish

        return publish.real_clients(self.conn, reel_id)

    def qa_review(self, reel_id: int) -> str:
        """A rendered reel's QA and review, after its worker died before the review."""
        from reelline.pipeline.produce import review_rendered

        return review_rendered(self.conn, reel_id, self.names, log=self.log)

    def produce(self, slot_date: str, slot_time: str, **kw):
        """The chain's render ... review (reelline/pipeline/slices.py run_produce())."""
        from reelline.pipeline.slices import run_produce

        return run_produce(self.conn, slot_date, slot_time, self.names, alert=False, log=self.log, **kw)

    def pick(self, slot_date: str, slot_time: str, menu: bool | None = None) -> int | str | None:
        """The slot's pick (pick.py run: assess, queued, ranked, maybe a menu): a pick
        id, "menu", or None (nothing eligible; pick.py alerted). A live pick the slot
        already has (a resumed chain) is returned as it is."""
        pick = db.slot_pick(self.conn, slot_date, slot_time)
        if pick is not None:
            return pick["id"]
        return self.repick(slot_date, slot_time, menu=menu)

    def repick(self, slot_date: str, slot_time: str, menu: bool | None = None) -> int | str | None:
        """The slot picked again: a pick id, "menu" (a menu went out), or None. menu None:
        a menu unless it's an evening-batch slot; False: never (after None of these)."""
        from reelline.steps.pick import pick_for_slot

        return pick_for_slot(self.conn, self.names, slot_date, slot_time, menu=menu, log=self.log)


RENDER_WAIT_LOG_S = 60.0  # the render-lock wait is logged this often


def run_job(conn: sqlite3.Connection, job_id: int, tg, steps, log: Callable[[str], None] = print) -> str:
    """Run one job to the end: done (the new review is out) or failed (reported)."""
    job = get_job(conn, job_id)
    if job is None or job["status"] not in ACTIVE:
        log(f"Job #{job_id} is {job['status'] if job else 'missing'}; nothing to do")
        return job["status"] if job else "missing"
    with db.transaction(conn):  # claim it: a second worker for the same job finds it taken
        claimed = conn.execute(
            "UPDATE jobs SET status = 'running', pid = ?, started_at = ?, attempts = attempts + 1 "
            "WHERE id = ? AND status = 'queued'",
            (os.getpid(), db.now_iso(), job_id),
        ).rowcount
    if not claimed:
        log(f"Job #{job_id} was taken by another worker; nothing to do")
        return "taken"

    def step(name: str) -> None:
        update_job(conn, job_id, step=name, step_at=db.now_iso())
        log(f"Job #{job_id}: {name}")

    logged = [-RENDER_WAIT_LOG_S]

    def waiting(who: str, waited_s: float) -> None:
        # The heartbeat: the step looks freshly started to the reaper while the job queues.
        update_job(conn, job_id, step_at=db.now_iso())
        if waited_s - logged[0] >= RENDER_WAIT_LOG_S:
            logged[0] = waited_s
            log(f"Job #{job_id}: waiting for the render lock (held by {who}), {int(waited_s // 60)} min so far")

    def acquired(waited_s: float) -> None:
        update_job(conn, job_id, step_at=db.now_iso())  # the wait isn't the step's time
        if waited_s >= 1:
            log(f"Job #{job_id}: got the render lock after {int(waited_s)} s")

    try:
        with db.cost_context(job_id=job_id), renderlock.observing(renderlock.Observer(waiting, acquired), job_id):
            # every paid call of the job carries its id (reelline/pipeline/costs.py)
            result = _run_kind(conn, job, steps, step, tg)
    except Exception as exc:
        log(traceback.format_exc())
        # A dropped connection's message names the call and its error type already.
        error = (str(exc) if isinstance(exc, TransportFailure) else f"{type(exc).__name__}: {exc}")[
            : conf().review.approve.job_error_chars
        ]
        update_job(conn, job_id, status="failed", error=error, finished_at=db.now_iso())
        report_failure(conn, tg, get_job(conn, job_id), log=log)
        return "failed"
    update_job(conn, job_id, status="done", result=result, finished_at=db.now_iso())
    log(f"Job #{job_id} done: {result}")
    report_recovery(conn, tg, get_job(conn, job_id), log=log)
    return "done"


def _run_kind(conn: sqlite3.Connection, job: sqlite3.Row, steps, step: Callable[[str], None], tg) -> str:
    kind, slot = job["kind"], (job["slot_date"], job["slot_time"])
    origin = review.get(conn, job["review_id"]) if job["review_id"] else None
    if kind in ("publish", "unschedule"):
        return _publish_kind(conn, job, steps, step, tg, origin)
    if kind in CHAIN_KINDS:
        from reelline.pipeline.run_daily import slot_reel

        held = slot_reel(conn, *slot)
        if held is not None:  # a Retry or a resume after the slot's reel was approved or went out
            return f"nothing to do: reel #{held['id']} ({held['status']}) holds {slot[0]} {slot[1]}"
    if kind == "pick":
        step("pick")
        detail = json.loads(job["detail"] or "{}")
        found = steps.pick(*slot, menu=False) if detail.get("menu") is False else steps.pick(*slot)
        if found == "menu":
            return "menu sent"
        if found is None:
            return "nothing eligible"
        live = conn.execute("SELECT * FROM picks WHERE id = ?", (found,)).fetchone()
        if detail.get("after_menu"):
            try:
                review.channel(tg).send(
                    pick_line(
                        conn,
                        live,
                        f"the best story outside menu {detail['after_menu']}; writing "
                        f"the script and the reel (job #{job['id']}).",
                    )
                )
            except NotifyError as exc:
                print(f"job #{job['id']}: send failed: {exc}", flush=True)
        return _continue(conn, tg, live, None, steps, step, {}, None, "scheduled")
    pick = conn.execute("SELECT * FROM picks WHERE id = ?", (job["pick_id"],)).fetchone()
    if kind == "other_story":
        step("drop the pick")
        if pick["status"] not in ("rejected", "skipped"):
            fate = job["story_fate"] or "rejected"
            with db.transaction(conn):
                db.update_pick(
                    conn,
                    pick["id"],
                    status="rejected",
                    note="dropped by the reviewer (other story)",
                    story_status="rejected" if fate == "rejected" else "new",
                    story_reason="reviewer: other story" if fate == "rejected" else None,
                )
                conn.execute(
                    "UPDATE reels SET status = 'superseded', updated_at = ? WHERE pick_id = ? AND status = 'rendered'",
                    (db.now_iso(), pick["id"]),
                )
        live = db.slot_pick(conn, *slot)
        if live is None:
            step("pick another story")
            found = steps.repick(*slot)
            if found is None:
                raise JobError(f"nothing eligible for {slot[0]} {slot[1]}")
            if found == "menu":
                _supersede(conn, tg, origin, "another story: a menu is out (answer it above)")
                return "menu sent"
            live = conn.execute("SELECT * FROM picks WHERE id = ?", (found,)).fetchone()
        return _continue(conn, tg, live, origin, steps, step, {}, None, "other story")
    if kind == "script_produce":
        return _continue(conn, tg, pick, origin, steps, step, {}, None, "script and reel")
    if kind == "qa_review":
        step("qa")
        return steps.qa_review(json.loads(job["detail"] or "{}")["reel_id"])
    # new_script / new_voice / new_images: same pick, same background.
    keep = (
        conn.execute("SELECT background FROM reels WHERE id = ?", (origin["reel_id"],)).fetchone()[0]
        if origin
        else None
    )
    if pick["status"] in ("rendered", "render_failed"):
        db.update_pick(conn, pick["id"], status="scripted")
    if kind == "new_script":
        step("script")
        steps.script(pick["id"])
        options = {}
    else:
        options = {"fresh_tts": True} if kind == "new_voice" else {"refresh_images": True}
    return _produce(conn, tg, pick, origin, steps, step, options, keep, JOB_LABELS[kind])


def _publish_kind(conn, job, steps, step, tg, origin) -> str:
    from reelline.pipeline import publish

    detail = json.loads(job["detail"] or "{}")
    reel_id = detail["reel_id"]
    reply = origin["message_id"] if origin else None
    if job["kind"] == "publish":
        step("publish")
        now = steps.now() if hasattr(steps, "now") else utcnow()
        out = publish.publish_reel(
            conn, reel_id, steps.publish_clients(reel_id), channels=detail.get("channels"), now=now
        )
        if not out.results:
            return "nothing to publish"
        text, markup = publish.outcome_text(conn, out, now)
        review.channel(tg).send(text, markup, reply_to=reply)
        ok = sum(1 for s, _ in out.results.values() if s != "failed")
        return f"{ok} of {len(out.results)} channel(s) scheduled"
    step("unschedule")
    errors = publish.unschedule(conn, reel_id, steps.publish_clients(reel_id))
    if errors:
        raise JobError("couldn't delete: " + "; ".join(errors) + " (the reel stays approved)")
    reopen_review(conn, tg, origin["id"], detail.get("by") or "?", steps.now() if hasattr(steps, "now") else utcnow())
    return "unscheduled"


def _continue(conn, tg, pick, origin, steps, step, options, keep, label) -> str:
    """A pick on to a reel from wherever it is (picked: the script first)."""
    if pick["status"] == "picked":
        step("script")
        steps.script(pick["id"])
    return _produce(
        conn,
        tg,
        conn.execute("SELECT * FROM picks WHERE id = ?", (pick["id"],)).fetchone(),
        origin,
        steps,
        step,
        options,
        keep,
        label,
    )


def _produce(conn, tg, pick, origin, steps, step, options, keep, label) -> str:
    step("produce")
    result = steps.produce(pick["slot_date"], pick["slot_time"], keep_background=keep, render_options=options or None)
    if result.status != "rendered":
        raise JobError(f"render failed: {result.error}")
    _supersede(conn, tg, origin, f"superseded by reel #{result.reel_id} ({label})")
    return f"reel #{result.reel_id}"


def _supersede(conn: sqlite3.Connection, tg, origin: sqlite3.Row | None, line: str) -> None:
    """The job's origin review, if still pending, is superseded (its message edited)."""
    if origin is None:
        return
    r = review.get(conn, origin["id"])
    if r["status"] == "pending":
        review.refresh(conn, tg, r["id"], status="superseded", state="main", line=f"🔁 {line}")
