"""The chains table: rows, the lock, the slot's job and reel."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from reelline.pipeline import db
from reelline.pipeline.run_daily.effects import Effects, once


def get_chain(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM chains WHERE slot_date = ? AND slot_time = ?", (slot_date, slot_time)).fetchone()


def set_chain(conn: sqlite3.Connection, chain_id: int, now: datetime, **fields) -> None:
    if "status" in fields and fields["status"] not in db.CHAIN_STATUSES:
        raise db.DbError(f"Unknown chain status {fields['status']!r}")
    if fields.get("status") in ("done", "skipped"):
        fields.setdefault("finished_at", db.now_iso(now))
    fields["updated_at"] = db.now_iso(now)
    with db.transaction(conn):
        conn.execute(
            f"UPDATE chains SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), chain_id)
        )


def new_chain(
    conn: sqlite3.Connection,
    slot_date: str,
    slot_time: str,
    now: datetime,
    status: str = "running",
    note: str | None = None,
    mode: str | None = None,
) -> sqlite3.Row:
    if mode not in db.CHAIN_MODES:
        raise db.DbError(f"Unknown chain mode {mode!r}")
    with db.transaction(conn):
        conn.execute(
            """INSERT OR IGNORE INTO chains (slot_date, slot_time, status, note, mode, started_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (slot_date, slot_time, status, note, mode, db.now_iso(now), db.now_iso(now)),
        )
    return get_chain(conn, slot_date, slot_time)


def acquire(conn: sqlite3.Connection, chain: sqlite3.Row, fx: Effects, now: datetime) -> bool:
    """The chain's lock for this process; a lock whose process is gone is released and reported."""
    me = os.getpid()
    held = chain["pid"]
    if held and held != me:
        if fx.pid_alive(held):
            fx.did(f"{chain['slot_date']} {chain['slot_time']}: chain locked by pid {held}; skipped")
            return False
        if once(conn, "stale_lock", f"{chain['id']}:{held}", now):
            fx.say(
                f"🔓 {chain['slot_date']} {chain['slot_time']}: a stale chain lock (pid {held}, heartbeat "
                f"{chain['heartbeat_at']}) was released; the chain goes on."
            )
        fx.did(f"{chain['slot_date']} {chain['slot_time']}: stale lock of pid {held} released")
    with db.transaction(conn):
        got = conn.execute(
            "UPDATE chains SET pid = ?, heartbeat_at = ? WHERE id = ? AND (pid IS NULL OR pid = ?)",
            (me, db.now_iso(now), chain["id"], held),
        ).rowcount
    return got == 1


def release(conn: sqlite3.Connection, chain_id: int) -> None:
    with db.transaction(conn):
        conn.execute("UPDATE chains SET pid = NULL WHERE id = ? AND pid = ?", (chain_id, os.getpid()))


def last_job(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs WHERE slot_date = ? AND slot_time = ? ORDER BY id DESC LIMIT 1", (slot_date, slot_time)
    ).fetchone()


def slot_reel(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> sqlite3.Row | None:
    """The reel that holds the slot, else None: approved for it (its pick on the slot), or
    gone out for it, early or not (its pick on the slot, or a publish job run for the
    slot). A slot a reel holds never gets another chain (_advance(),
    fallback_due(), approve_bot._run_kind())."""
    return conn.execute(
        """SELECT * FROM reels WHERE (status IN ('approved', 'published', 'partially_published', 'deleted')
               AND pick_id IN (SELECT id FROM picks WHERE slot_date = ? AND slot_time = ?))
           OR (status IN ('published', 'partially_published', 'deleted')
               AND id IN (SELECT json_extract(detail, '$.reel_id') FROM jobs WHERE kind = 'publish'
                          AND slot_date = ? AND slot_time = ?))
           ORDER BY id DESC LIMIT 1""",
        (slot_date, slot_time, slot_date, slot_time),
    ).fetchone()
