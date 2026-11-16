"""Advancing one slot's chain."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.run_daily.base import CHAIN_KINDS, DIED, hm
from reelline.pipeline.run_daily.chains import acquire, get_chain, last_job, new_chain, release, set_chain, slot_reel
from reelline.pipeline.run_daily.effects import Effects, once
from reelline.pipeline.run_daily.ingest import ingest_state, start_ingest
from reelline.pipeline.run_daily.slots import (
    batch_slot,
    chain_start,
    review_held,
    same_day_start,
    slot_dt,
)


def advance(conn: sqlite3.Connection, slot_date: str, slot_time: str, fx: Effects, *, force: bool = False) -> str:
    """Start or move on one slot's chain; what it did (one word)."""
    now = fx.now()
    sdt = slot_dt(slot_date, slot_time)
    late = sdt - timedelta(hours=conf().steps.run_daily.catch_up_until_before_h)
    label = f"{slot_date} {slot_time}"
    chain = get_chain(conn, slot_date, slot_time)
    if chain is None:
        if not force and now < chain_start(slot_date, slot_time):
            return "not yet"
        if (
            db.slot_pick(conn, slot_date, slot_time) is None
            and not _menu_open(conn, slot_date, slot_time)
            and not force
            and now >= late
        ):
            chain = new_chain(conn, slot_date, slot_time, now, "skipped", "too late to start")
            if once(conn, "skipped", label, now):
                fx.say(
                    f"⏭ {label}: the chain couldn't start before {hm(late)} (the machine was off?): the slot is "
                    "skipped, nothing will post for it."
                )
            fx.did(f"{label}: skipped (too late)")
            return "skipped"
        mode = None
        if batch_slot(slot_date):
            mode = "fallback" if now >= same_day_start(slot_date, slot_time) else "evening"
        chain = new_chain(conn, slot_date, slot_time, now, mode=mode)
        fx.did(f"{label}: chain started" + (f" ({mode})" if mode else ""))
    if chain["status"] in ("done", "skipped") and not fallback_due(conn, chain, now):
        return chain["status"]
    if not acquire(conn, chain, fx, now):
        return "locked"
    try:
        chain = get_chain(conn, slot_date, slot_time)
        if fallback_due(conn, chain, now):
            fall_back(conn, chain, fx, now)
            chain = get_chain(conn, slot_date, slot_time)
        return _advance(conn, chain, fx, now, late, force)
    finally:
        release(conn, chain["id"])


def fallback_due(conn: sqlite3.Connection, chain: sqlite3.Row, now: datetime) -> bool:
    """An evening chain with no review (and nothing running) at its same-day start, while
    there's still time before the catch-up limit."""
    from reelline.pipeline import approve_bot as ab

    d, t = chain["slot_date"], chain["slot_time"]
    if chain["mode"] != "evening" or not batch_slot(d):
        return False
    late = slot_dt(d, t) - timedelta(hours=conf().steps.run_daily.catch_up_until_before_h)
    if (
        not same_day_start(d, t) <= now < late
        or ab.active_job(conn, d, t) is not None
        or slot_reel(conn, d, t) is not None
    ):
        return False
    pick = db.slot_pick(conn, d, t)
    return pick is None or pick["status"] in ("picked", "scripted", "render_failed")


def fall_back(conn: sqlite3.Connection, chain: sqlite3.Row, fx: Effects, now: datetime) -> None:
    """The evening chain becomes the same-day one, silently: a new job after a failed or
    empty one (the old one's Retry is moot), else the chain goes on from the pick's state."""
    d, t = chain["slot_date"], chain["slot_time"]
    set_chain(
        conn,
        chain["id"],
        now,
        mode="fallback",
        status="running",
        resumes=0,
        finished_at=None,
        note="fallback: no review by the same-day start",
    )
    job, pick = last_job(conn, d, t), db.slot_pick(conn, d, t)
    stuck = job is not None and (
        job["status"] == "failed"
        or (job["kind"] == "pick" and job["status"] == "done" and job["result"] == "nothing eligible")
    )
    if stuck:
        kind = "script_produce" if pick is not None else "pick"
        new = _add(conn, kind, d, t, pick)
        set_chain(conn, chain["id"], now, step=kind)
        fx.did(f"{d} {t}: fallback, {kind} job #{new} (after job #{job['id']}: {job['status']})")
    else:
        fx.did(f"{d} {t}: fallback")


def _menu_open(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> bool:
    return any((m["slot_date"], m["slot_time"]) == (slot_date, slot_time) for m in db.open_menus(conn))


def _advance(conn, chain, fx: Effects, now: datetime, late: datetime, force: bool) -> str:
    from reelline.pipeline import approve_bot as ab

    d, t = chain["slot_date"], chain["slot_time"]
    label = f"{d} {t}"
    active = ab.active_job(conn, d, t)
    if active is not None and active["status"] == "running" and not fx.job_alive(active["pid"], active["id"]):
        ab.update_job(conn, active["id"], status="failed", error=DIED, finished_at=db.now_iso(now))
        fx.did(f"{label}: job #{active['id']} ({active['kind']}) died at step {active['step']}")
        active = None
    if active is not None:
        set_chain(conn, chain["id"], now, status="running", step=active["kind"])
        return "running"
    pick = db.slot_pick(conn, d, t)
    held = slot_reel(conn, d, t)
    if held is not None and (pick is None or pick["id"] != held["pick_id"]):
        # Another reel holds the slot (gone out early, its pick moved off it): nothing
        # is picked, written or rendered for it again, whatever pick or job is left there.
        set_chain(
            conn,
            chain["id"],
            now,
            status="done",
            step="filled",
            note=f"reel #{held['id']} {held['status']} for this slot (pick #{held['pick_id']})",
        )
        fx.did(f"{label}: reel #{held['id']} ({held['status']}) holds the slot; chain done")
        return "done"
    job = last_job(conn, d, t)
    failed = job is not None and job["status"] == "failed"
    if failed and (pick is None or pick["status"] in ("picked", "scripted", "render_failed")):
        died = (job["error"] or "").startswith((DIED, "the worker process exited"))
        if died and job["kind"] in CHAIN_KINDS:
            if chain["resumes"] >= conf().steps.run_daily.max_resumes:
                set_chain(
                    conn,
                    chain["id"],
                    now,
                    status="failed",
                    step=job["kind"],
                    note=f"job #{job['id']} died {chain['resumes'] + 1} times",
                )
                if once(conn, "resumes_exhausted", str(chain["id"]), now) and fx.tg is not None:
                    ab.report_failure(conn, fx.tg, ab.get_job(conn, job["id"]))
                return "failed"
            kind = "script_produce" if pick is not None else "pick"
            new = _add(conn, kind, d, t, pick)
            set_chain(conn, chain["id"], now, status="running", step=kind, resumes=chain["resumes"] + 1)
            fx.say(
                f"🔁 {label}: job #{job['id']} ({job['kind']}) died at step {job['step'] or '-'}; resumed from the "
                f"pick's state as job #{new}."
            )
            fx.did(f"{label}: resumed job #{job['id']} as #{new}")
            return "resumed"
        set_chain(conn, chain["id"], now, status="failed", step=job["kind"], note=f"job #{job['id']}: {job['error']}")
        return "failed"  # the job's failure report has the Retry button
    if pick is None:
        if _menu_open(conn, d, t):
            set_chain(conn, chain["id"], now, status="waiting", step="menu")
            return "waiting"
        if (
            job is not None
            and job["kind"] == "pick"
            and job["status"] == "done"
            and job["result"] == "nothing eligible"
        ):
            set_chain(conn, chain["id"], now, status="skipped", step="pick", note="nothing eligible")
            return "skipped"
        if not force and now >= late:
            set_chain(conn, chain["id"], now, status="skipped", note="no pick before the catch-up limit")
            if once(conn, "skipped", label, now):
                fx.say(f"⏭ {label}: no pick by {hm(late)}: the slot is skipped, nothing will post for it.")
            return "skipped"
        ingest = ingest_state(conn, fx, now)
        if ingest == "running":
            set_chain(conn, chain["id"], now, status="running", step="ingest")
            return "ingest running"
        if ingest == "stale":
            start_ingest(conn, fx, now)
            set_chain(conn, chain["id"], now, status="running", step="ingest")
            return "ingest started"
        new = _add(conn, "pick", d, t, None)
        set_chain(conn, chain["id"], now, status="running", step="pick")
        fx.did(f"{label}: pick job #{new}")
        return "pick"
    if pick["status"] in ("picked", "scripted", "render_failed"):
        new = _add(conn, "script_produce", d, t, pick)
        set_chain(conn, chain["id"], now, status="running", step="script_produce")
        fx.did(f"{label}: script_produce job #{new} (pick #{pick['id']} {pick['status']})")
        return "script_produce"
    reel = unreviewed_reel(conn, pick, now)
    if reel is not None:
        if once(conn, "qa_review", str(reel["id"]), now):
            new = ab.add_job(conn, "qa_review", pick, detail={"reel_id": reel["id"]})
            set_chain(conn, chain["id"], now, status="running", step="qa_review")
            fx.say(
                f"🩹 {label}: reel #{reel['id']} rendered, but its worker stopped before the review; QA and the "
                f"review run as job #{new}."
            )
            fx.did(f"{label}: qa_review job #{new} (reel #{reel['id']})")
            return "qa_review"
        if once(conn, "no_review", label, now):
            why = f"job #{job['id']}: {job['error'] or job['status']}" if job is not None else "no job"
            fx.say(
                f"⚠️ {label}: the chain closes without a review: reel #{reel['id']} rendered, none sent ({why}). "
                f"`python -m reelline.pipeline.produce review {reel['id']} --video` sends one."
            )
    set_chain(conn, chain["id"], now, status="done", step="review" if pick["status"] == "rendered" else pick["status"])
    return "done"


def unreviewed_reel(conn: sqlite3.Connection, pick: sqlite3.Row, now: datetime) -> sqlite3.Row | None:
    """A rendered pick's latest rendered reel when no review of it was ever sent and it
    isn't held for the evening delivery, else None."""
    from reelline.review import machine as review

    if pick["status"] != "rendered" or review_held(conn, pick["slot_date"], pick["slot_time"], now):
        return None
    reel = conn.execute(
        "SELECT * FROM reels WHERE pick_id = ? AND status = 'rendered' ORDER BY id DESC LIMIT 1", (pick["id"],)
    ).fetchone()
    return reel if reel is not None and review.for_reel(conn, reel["id"]) is None else None


def review_out(conn: sqlite3.Connection, pick: sqlite3.Row) -> bool:
    """A review of the pick is in the chat, pending or approved."""
    return (
        conn.execute(
            "SELECT 1 FROM reviews WHERE pick_id = ? AND status IN ('pending', 'approved')", (pick["id"],)
        ).fetchone()
        is not None
    )


def _add(conn, kind: str, d: str, t: str, pick) -> int | None:
    from reelline.pipeline import approve_bot as ab

    return ab.add_job(conn, kind, pick if pick is not None else {"slot_date": d, "slot_time": t, "id": None})
