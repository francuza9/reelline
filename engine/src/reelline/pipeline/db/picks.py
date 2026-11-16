"""Picks: one row per story chosen for a slot."""

from __future__ import annotations

import json
import sqlite3

from reelline.pipeline import db as _db
from reelline.pipeline.db.base import CHOSEN_BY, PICK_STATUSES, DbError
from reelline.pipeline.db.stories import set_story_status


def add_pick(
    conn: sqlite3.Connection,
    story_id: int,
    status: str,
    chosen_by: str,
    candidates: list,
    effective_score: float | None,
    slot_date: str | None = None,
    slot_time: str | None = None,
    menu_id: int | None = None,
    queue_position: int | None = None,
    note: str | None = None,
) -> int:
    """Record a pick and set the story's status to match (picked / queued)."""
    if status not in ("picked", "queued"):
        raise DbError(f"A new pick is picked or queued, not {status!r}")
    if chosen_by not in CHOSEN_BY:
        raise DbError(f"chosen_by must be one of {', '.join(CHOSEN_BY)}")
    if (status == "picked") != (slot_date is not None and slot_time is not None):
        raise DbError("A picked story has a slot; a queued one doesn't")
    at = _db.now_iso()
    try:
        with _db.transaction(conn):
            cur = conn.execute(
                """INSERT INTO picks (story_id, slot_date, slot_time, status, effective_score, chosen_by, candidates,
                       menu_id, queue_position, note, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_id,
                    slot_date,
                    slot_time,
                    status,
                    effective_score,
                    chosen_by,
                    json.dumps(candidates, ensure_ascii=False),
                    menu_id,
                    queue_position,
                    note,
                    at,
                    at,
                ),
            )
            set_story_status(conn, story_id, status)
    except sqlite3.IntegrityError as exc:
        raise DbError(f"Can't record pick of story {story_id} for {slot_date} {slot_time}: {exc}") from exc
    return cur.lastrowid


def update_pick(
    conn: sqlite3.Connection, pick_id: int, story_status: str | None = None, story_reason: str | None = None, **fields
) -> None:
    """Change a pick's fields (status, slot_date, slot_time, note, ...) and optionally its story's status."""
    if "status" in fields and fields["status"] not in PICK_STATUSES:
        raise DbError(f"Unknown pick status {fields['status']!r}")
    fields["updated_at"] = _db.now_iso()
    with _db.transaction(conn):
        row = conn.execute("SELECT story_id FROM picks WHERE id = ?", (pick_id,)).fetchone()
        if row is None:
            raise DbError(f"No pick with id {pick_id}")
        try:
            conn.execute(
                f"UPDATE picks SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), pick_id)
            )
        except sqlite3.IntegrityError as exc:
            raise DbError(f"Can't update pick {pick_id}: {exc}") from exc
        if story_status:
            set_story_status(conn, row["story_id"], story_status, story_reason)


def slot_pick(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM picks WHERE slot_date = ? AND slot_time = ? AND status NOT IN ('queued', 'rejected')",
        (slot_date, slot_time),
    ).fetchone()


def queued_picks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM picks WHERE status = 'queued' ORDER BY queue_position, id").fetchall()


def next_queue_position(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(queue_position), 0) + 1 FROM picks").fetchone()[0]
