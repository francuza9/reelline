"""The bot's /status."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline.approve_bot.base import (
    JOB_LABELS,
    hm,
)
from reelline.pipeline.approve_bot.jobs import (
    active_job,
)
from reelline.pipeline.scoring import tbilisi
from reelline.review import machine as review


def expires_at(sent: datetime, slot: datetime) -> datetime:
    """A pending review expires review.approve.expire_after_slot_h after its slot, and never
    sooner than review.approve.expire_after_h after it was sent."""
    return max(
        sent + timedelta(hours=conf().review.approve.expire_after_h),
        slot + timedelta(hours=conf().review.approve.expire_after_slot_h),
    )


def status_text(conn: sqlite3.Connection, now: datetime) -> str:
    """Today's and tomorrow's slots, any running job, and whether the schedule is paused."""
    today = now.astimezone(tbilisi()).date()
    lines = [f"Status {hm(now)} (Tbilisi)"]
    paused = db.get_state(conn, "paused")
    if paused:
        lines.append(f"⏸ daily schedule paused since {hm(datetime.fromisoformat(paused))} (/resume)")
    from reelline.pipeline import run_daily

    if conf().steps.run_daily.evening_batch_from:
        lines.append(
            f"Evening batch from {conf().steps.run_daily.evening_batch_from}: chains {conf().steps.run_daily.evening_start_at} "
            f"the day before, reviews {conf().steps.run_daily.evening_delivery_at}"
        )
    for day in (today, today + timedelta(days=1)):
        for slot in run_daily.day_slots(conn, day.isoformat()):
            d = day.isoformat()
            chain = run_daily.get_chain(conn, d, slot)
            if chain is None and now < run_daily.chain_start(d, slot):
                start = run_daily.chain_start(d, slot).astimezone(tbilisi())
                extra = f" · chain starts {start:%m-%d %H:%M}" + (" (evening)" if run_daily.batch_slot(d) else "")
            elif chain is not None and chain["mode"]:
                extra = f" · {chain['mode']} chain {chain['status']}"
            else:
                extra = ""
            lines.append(f"{d} {slot}: {slot_state(conn, d, slot)}{extra}")
            job = active_job(conn, day.isoformat(), slot)
            if job is not None:
                since = hm(datetime.fromisoformat(job["started_at"])) if job["started_at"] else "queued"
                lines.append(
                    f"  ⏳ job #{job['id']} {JOB_LABELS[job['kind']]}, step {job['step'] or '-'}, since {since}"
                )
    return "\n".join(lines)


def _review_held(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> bool:
    from reelline.pipeline.run_daily import review_held, utcnow

    return review_held(conn, slot_date, slot_time, utcnow())


def slot_picking(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> bool:
    """The slot's chain is under way without a pick yet (running or waiting: ingest, the
    pick job, an open menu, an evening batch's chain), or it has an active job or an open
    menu. Next free slot passes it by: a pick moved in would collide with the one about
    to be made there (one live pick per slot), failing that job or the menu's answer."""
    from reelline.pipeline.run_daily import get_chain

    chain = get_chain(conn, slot_date, slot_time)
    return (
        (chain is not None and chain["status"] in ("running", "waiting"))
        or active_job(conn, slot_date, slot_time) is not None
        or any((m["slot_date"], m["slot_time"]) == (slot_date, slot_time) for m in db.open_menus(conn))
    )


def slot_state(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> str:
    pick = db.slot_pick(conn, slot_date, slot_time)
    if pick is None:
        menu = next(
            (m for m in db.open_menus(conn) if (m["slot_date"], m["slot_time"]) == (slot_date, slot_time)), None
        )
        if menu is not None:
            return f"menu open (until {review.local_hm(menu['deadline_at'])})"
        skipped = conn.execute(
            "SELECT 1 FROM picks WHERE slot_date = ? AND slot_time = ? AND status = 'skipped'", (slot_date, slot_time)
        ).fetchone()
        return "skipped" if skipped else "no pick"
    reel = conn.execute(
        "SELECT * FROM reels WHERE pick_id = ? AND status != 'superseded' ORDER BY id DESC LIMIT 1", (pick["id"],)
    ).fetchone()
    status = pick["status"]
    if status == "rendered" and reel is not None:
        r = review.for_reel(conn, reel["id"])
        if reel["status"] == "expired":
            return about_reel(reel["id"], "expired")
        qa = {"pass": " (QA ✅)", "fail": " (QA ❌)"}.get(reel["qa_status"], "")
        if r is None and _review_held(conn, slot_date, slot_time):
            return about_reel(reel["id"], f"rendered{qa}, review held for the evening delivery")
        return about_reel(reel["id"], f"awaiting review{qa}" if r and r["status"] == "pending" else "rendered")
    if reel is not None and reel["status"] in ("published", "partially_published", "deleted"):
        return about_reel(reel["id"], reel["status"].replace("_", " "))
    if status == "approved" and reel is not None:
        when = "now" if reel["publish_mode"] == "now" else review.local_hm(reel["publish_at"])
        return about_reel(reel["id"], f"approved, publishes {when}")
    return {
        "picked": "picked",
        "scripted": "scripted",
        "render_failed": "render failed",
        "posted": "published",
        "skipped": "skipped",
    }.get(status, status)
