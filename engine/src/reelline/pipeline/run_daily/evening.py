"""The evening summary and the held reviews' delivery."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.run_daily.effects import Effects, once
from reelline.pipeline.run_daily.slots import (
    batch_slot,
    claim_review,
    day_slots,
    delivery_at,
    slot_dt,
)
from reelline.pipeline.scoring import tbilisi


def evening_text(conn: sqlite3.Connection, now: datetime) -> str:
    """The evening summary: today's posts and their links, the views line, today's spend,
    tomorrow's slot(s) with their titles."""
    from reelline.pipeline import analytics, costs
    from reelline.pipeline import approve_bot as ab

    today = now.astimezone(tbilisi()).date()
    tomorrow = today + timedelta(days=1)
    lines = [f"🌙 {conf().channel.shown_name()}, {today.isoformat()}", "", "Posted today:"]
    lines += posted_lines(conn, today.isoformat()) or ["  nothing"]
    try:
        lines.append(analytics.morning_line(analytics.load(conn, now)))
    except Exception as exc:  # the stats never hold the summary back
        lines.append(f"📈 Views: n/a ({type(exc).__name__})")
    month = costs.month_of(now)
    lines.append(f"Spend today: ${costs.day_total(conn, today):.2f} (month so far ${costs.sql_total(conn, month):.2f})")
    lines += ["", f"Tomorrow ({tomorrow.isoformat()}):"]
    for t in day_slots(conn, tomorrow.isoformat()):
        title = slot_title(conn, tomorrow.isoformat(), t)
        lines.append(
            f"  {t} {title}: {ab.slot_state(conn, tomorrow.isoformat(), t)}"
            if title
            else f"  {t}: {ab.slot_state(conn, tomorrow.isoformat(), t)}"
        )
    return "\n".join(lines)


def slot_title(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> str | None:
    """The slot's live pick's title: its reel's short title once there is one, else the story's."""
    from reelline.review.machine import short_title

    pick = db.slot_pick(conn, slot_date, slot_time)
    if pick is None:
        return None
    reel = conn.execute(
        "SELECT * FROM reels WHERE pick_id = ? AND status != 'superseded' ORDER BY id DESC LIMIT 1", (pick["id"],)
    ).fetchone()
    if reel is not None:
        return short_title(conn, reel)
    story = db.get_story(conn, pick["story_id"])
    return story["title"] if story else None


def posted_lines(conn: sqlite3.Connection, day: str) -> list[str]:
    """The reels that went out on `day` (Tbilisi) and their links, in time order: by when
    they went live (their slot's day when that isn't known), so an early post is listed
    on the day and at the time it went out, not at its slot's."""
    from reelline.pipeline.publish import went_out_at
    from reelline.review.machine import short_title

    found = []
    for reel in conn.execute("""SELECT r.*, p.slot_date, p.slot_time FROM reels r JOIN picks p ON p.id = r.pick_id
                                WHERE r.status IN ('published', 'partially_published') ORDER BY r.id""").fetchall():
        at = went_out_at(conn, reel["id"])
        local = at.astimezone(tbilisi()) if at is not None else None
        if (local.date().isoformat() if local else reel["slot_date"]) != day:
            continue
        on_time = local is None or (reel["slot_date"] == day and local >= slot_dt(reel["slot_date"], reel["slot_time"]))
        hhmm = reel["slot_time"] if on_time else local.strftime("%H:%M")
        found.append((hhmm, reel))
    out = []
    for hhmm, reel in sorted(found, key=lambda x: x[0]):
        links = conn.execute(
            "SELECT channel, url FROM publications WHERE reel_id = ? AND state = 'published' AND url "
            "IS NOT NULL ORDER BY channel",
            (reel["id"],),
        ).fetchall()
        out.append(f"  {hhmm} {short_title(conn, reel)}")
        out += [f"    {conf().publish.labels.get(r['channel'], r['channel'])}: {r['url']}" for r in links]
    return out


def held_reel(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> sqlite3.Row | None:
    """The slot's rendered reel still without a review, once its worker is done."""
    from reelline.pipeline import approve_bot as ab
    from reelline.review import machine as review

    pick = db.slot_pick(conn, slot_date, slot_time)
    if pick is None or pick["status"] != "rendered" or ab.active_job(conn, slot_date, slot_time) is not None:
        return None
    reel = conn.execute(
        "SELECT * FROM reels WHERE pick_id = ? AND status = 'rendered' ORDER BY id DESC LIMIT 1", (pick["id"],)
    ).fetchone()
    return reel if reel is not None and review.for_reel(conn, reel["id"]) is None else None


def deliver(conn: sqlite3.Connection, fx: Effects, now: datetime, summary: bool = True) -> None:
    """From the evening delivery time: the summary once (that evening only), then every
    held review of the slots it's for, in slot order."""
    today = now.astimezone(tbilisi()).date()
    for day in (today, today + timedelta(days=1)):
        d = day.isoformat()
        if not batch_slot(d) or now < delivery_at(d):
            continue
        evening = delivery_at(d).astimezone(tbilisi()).date()
        if summary and today == evening and once(conn, "evening", d, now):
            fx.say(evening_text(conn, now))
            fx.did("evening summary sent")
        for t in day_slots(conn, d):
            reel = held_reel(conn, d, t)
            if reel is not None and claim_review(conn, reel["id"], now):
                fx.deliver_review(conn, reel["id"])
                fx.did(f"{d} {t}: review of reel #{reel['id']} sent")
