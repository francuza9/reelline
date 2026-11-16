"""Slot times, the evening batch, which slots a day has."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from reelline.config import conf
from reelline.pipeline.run_daily.chains import get_chain
from reelline.pipeline.run_daily.effects import once
from reelline.pipeline.scoring import slot_datetime, tbilisi


def slot_dt(slot_date: str, slot_time: str) -> datetime:
    return slot_datetime(date.fromisoformat(slot_date), slot_time)


def batch_slot(slot_date: str) -> bool:
    """An evening-batch slot (steps.run_daily.evening_batch_from or later), not a same-day one."""
    start = conf().steps.run_daily.evening_batch_from
    return start is not None and slot_date >= start


def evening_of(slot_date: str, hhmm: str) -> datetime:
    """hhmm (Tbilisi) on the day before slot_date."""
    return slot_datetime(date.fromisoformat(slot_date) - timedelta(days=1), hhmm)


def same_day_start(slot_date: str, slot_time: str) -> datetime:
    """The same-day chain's start; for an evening-batch slot, its fallback time."""
    return slot_dt(slot_date, slot_time) - timedelta(hours=conf().steps.run_daily.chain_start_before_h)


def delivery_at(slot_date: str) -> datetime:
    """When an evening-batch slot's held review goes out: the evening before."""
    return evening_of(slot_date, conf().steps.run_daily.evening_delivery_at)


def chain_start(slot_date: str, slot_time: str) -> datetime:
    o = conf().steps.run_daily.test_override
    if o and (o["slot_date"], o["slot_time"]) == (slot_date, slot_time):
        return datetime.fromisoformat(o["start_at"])
    if batch_slot(slot_date):
        return evening_of(slot_date, conf().steps.run_daily.evening_start_at)
    return same_day_start(slot_date, slot_time)


def chain_mode(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> str | None:
    chain = get_chain(conn, slot_date, slot_time)
    return chain["mode"] if chain is not None else None


def review_held(conn: sqlite3.Connection, slot_date: str, slot_time: str, now: datetime) -> bool:
    """An evening chain's review waits for the evening delivery."""
    return chain_mode(conn, slot_date, slot_time) == "evening" and now < delivery_at(slot_date)


def claim_review(conn: sqlite3.Connection, reel_id: int, now: datetime) -> bool:
    """True for the one sender of a reel's first review (its worker, or the evening delivery)."""
    return once(conn, "review_sent", str(reel_id), now)


def day_slots(conn: sqlite3.Connection, day: str) -> list[str]:
    """The day's slots for /status, the evening summary and the evening delivery:
    channel.slots, plus any other slot time that has a chain or a pick on its way (not
    posted yet) that day (a slot dropped from channel.slots mid-way stays listed)."""
    other = {
        r[0]
        for r in conn.execute(
            """SELECT slot_time FROM chains WHERE slot_date = ? UNION
           SELECT slot_time FROM picks WHERE slot_date = ? AND status IN ('picked', 'scripted', 'rendered',
               'render_failed', 'approved')""",
            (day, day),
        )
    }
    return sorted(set(conf().channel.slots) | other)


def candidate_slots(now: datetime) -> list[tuple[str, str]]:
    """Today's and tomorrow's slots (Tbilisi) whose chain may start by now, plus the override."""
    today = now.astimezone(tbilisi()).date()
    out = [
        (d.isoformat(), t)
        for d in (today, today + timedelta(days=1))
        for t in conf().channel.slots
        if chain_start(d.isoformat(), t) <= now and (d == today or slot_dt(d.isoformat(), t) > now)
    ]
    o = conf().steps.run_daily.test_override
    if o and (o["slot_date"], o["slot_time"]) not in out and chain_start(o["slot_date"], o["slot_time"]) <= now:
        out.append((o["slot_date"], o["slot_time"]))
    return out
