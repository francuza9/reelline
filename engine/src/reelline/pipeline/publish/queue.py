"""Publication rows: reading, updating, enqueueing and the due list."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from reelline.config import conf
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline.publish.base import CHANNELS, enabled_channels, label, local_hm, local_when, utcnow
from reelline.pipeline.publish.guard import guard, reel_row


def publications(conn: sqlite3.Connection, reel_id: int) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM publications WHERE reel_id = ?", (reel_id,)).fetchall()
    return sorted(rows, key=lambda p: CHANNELS.index(p["channel"]))


def get_pub(conn: sqlite3.Connection, pub_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()


def set_pub(conn: sqlite3.Connection, pub_id: int, now: datetime | None = None, **fields) -> None:
    if "state" in fields and fields["state"] not in db.PUBLICATION_STATES:
        raise db.DbError(f"Unknown publication state {fields['state']!r}")
    fields["updated_at"] = db.now_iso(now)
    with db.transaction(conn):
        conn.execute(
            f"UPDATE publications SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), pub_id)
        )


def set_reel(conn: sqlite3.Connection, reel_id: int, now: datetime | None = None, **fields) -> None:
    fields["updated_at"] = db.now_iso(now)
    with db.transaction(conn):
        conn.execute(
            f"UPDATE reels SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), reel_id)
        )


def dry_run_text(reel: sqlite3.Row) -> str:
    from reelline.pipeline.scoring import tbilisi

    local = datetime.fromisoformat(reel["publish_at"]).astimezone(tbilisi()).strftime("%Y-%m-%d %H:%M")
    when = "now" if reel["publish_mode"] == "now" else f"at {local}"
    return about_reel(reel["id"], f"publishing disabled - dry run (would publish {when})")


def enqueue(
    conn: sqlite3.Connection,
    reel_id: int,
    send: Callable[[str], object],
    log: Callable[[str], None] = print,
    now: datetime | None = None,
    probe: Callable[[Path], dict] | None = None,
) -> str:
    """Approve's hook: the guard, then one waiting publication per enabled channel
    (a re-approval after Unschedule reuses them). Returns what it said in the chat."""
    now = now or utcnow()
    reel = reel_row(conn, reel_id)
    when = "now" if reel["publish_mode"] == "now" else f"at {local_hm(datetime.fromisoformat(reel['publish_at']))}"
    log(f"publish.enqueue: reel #{reel_id} ({reel['name']}), {when}")
    if not conf().publish.enabled:
        text = dry_run_text(reel)
        send(text)
        return text
    channels = enabled_channels()
    if not channels:
        text = about_reel(reel_id, "⚠️ No publishing channel enabled (publish.channels); nothing scheduled")
        send(text)
        return text
    problems = guard(conn, reel, channels, probe)
    if problems:
        text = about_reel(reel_id, f"❌ Can't be published: {'; '.join(problems)}. Nothing was uploaded.")
        send(text)
        return text
    at = db.now_iso(now)
    with db.transaction(conn):
        for ch in channels:
            conn.execute(
                """INSERT INTO publications (reel_id, channel, state, due_at, mode, created_at, updated_at)
                   VALUES (?, ?, 'waiting', ?, ?, ?, ?)
                   ON CONFLICT (reel_id, channel) DO UPDATE SET state = 'waiting', due_at = excluded.due_at,
                       mode = excluded.mode, error = NULL, updated_at = excluded.updated_at
                   WHERE publications.state IN ('waiting', 'unscheduled')""",
                (reel_id, ch, reel["publish_at"], reel["publish_mode"], at, at),
            )
    slot = datetime.fromisoformat(reel["publish_at"])
    create_at = slot - timedelta(hours=conf().publish.buffer_lead_h)
    names = ", ".join(label(c) for c in channels)
    if reel["publish_mode"] == "now":
        text = about_reel(reel_id, f"📤 Posting now on {names}.")
    elif create_at <= now:
        text = about_reel(reel_id, f"📤 Scheduling on {names} for {local_when(slot, now)}.")
    else:
        text = about_reel(
            reel_id,
            f"📤 Goes out {local_when(slot, now)} on {names}; the posts are created "
            f"{local_when(create_at, now)} ({conf().publish.buffer_lead_h} h before).",
        )
    send(text)
    return text


def due(conn: sqlite3.Connection, now: datetime) -> list[int]:
    """Reels with waiting publications (or ones a dead job left scheduling) whose time
    to be created has come."""
    if not conf().publish.enabled:
        return []
    lead = timedelta(hours=conf().publish.buffer_lead_h)
    out = []
    for r in conn.execute("""SELECT DISTINCT r.id, r.publish_at, r.publish_mode FROM publications p
                             JOIN reels r ON r.id = p.reel_id
                             WHERE p.state IN ('waiting', 'scheduling')
                                 AND r.status IN ('approved', 'partially_published')
                             ORDER BY r.id""").fetchall():
        if r["publish_mode"] == "now" or datetime.fromisoformat(r["publish_at"]) - lead <= now:
            out.append(r["id"])
    return out
