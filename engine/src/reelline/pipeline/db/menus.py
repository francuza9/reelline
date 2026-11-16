"""Pick menus, dismissals and the bot's key-value state."""

from __future__ import annotations

import json
import sqlite3

from reelline.pipeline import db as _db
from reelline.pipeline.db.assessments import get_story
from reelline.pipeline.db.base import DISMISS_REASONS, MENU_STATUSES, DbError
from reelline.pipeline.db.stories import set_story_status


def add_menu(conn: sqlite3.Connection, slot_date: str, slot_time: str, candidates: list, deadline_at: str) -> int:
    with _db.transaction(conn):
        cur = conn.execute(
            """INSERT INTO menus (slot_date, slot_time, status, created_at, deadline_at, candidates)
               VALUES (?, ?, 'open', ?, ?, ?)""",
            (slot_date, slot_time, _db.now_iso(), deadline_at, json.dumps(candidates, ensure_ascii=False)),
        )
    return cur.lastrowid


def get_menu(conn: sqlite3.Connection, menu_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM menus WHERE id = ?", (menu_id,)).fetchone()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    """A bot_state value (offset, heartbeat, paused, ...)."""
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    with _db.transaction(conn):
        if value is None:
            conn.execute("DELETE FROM bot_state WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO bot_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def dismiss_story(
    conn: sqlite3.Connection,
    story_id: int,
    reason: str,
    menu_id: int | None,
    slot_date: str | None,
    slot_time: str | None,
    by: str | None = "human",
    at: str | None = None,
    note: str | None = None,
) -> None:
    """Dismiss a story for good: a dismissals row and story status dismissed (reason as
    the status reason). Only a story still "new" can be dismissed."""
    if reason not in DISMISS_REASONS:
        raise DbError(f"Unknown dismiss reason {reason!r} (one of {', '.join(DISMISS_REASONS)})")
    with _db.transaction(conn):
        story = get_story(conn, story_id)
        if story is None or story["status"] != "new":
            raise DbError(
                f"Story {story_id} can't be dismissed: it is {'missing' if story is None else story['status']}"
            )
        conn.execute(
            """INSERT INTO dismissals (story_id, menu_id, reason, slot_date, slot_time, by, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (story_id, menu_id, reason, slot_date, slot_time, by, note, at or _db.now_iso()),
        )
        set_story_status(conn, story_id, "dismissed", reason)


def dismissed(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every dismissed story with what twin-matching needs (URL, title, program key)."""
    return conn.execute(
        """SELECT d.*, s.canonical_url, s.title, a.same_program_key, a.topic FROM dismissals d
           JOIN stories s ON s.id = d.story_id LEFT JOIN assessments a ON a.story_id = d.story_id
           ORDER BY d.id"""
    ).fetchall()


def open_menus(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM menus WHERE status = 'open' ORDER BY id").fetchall()


def update_menu(conn: sqlite3.Connection, menu_id: int, **fields) -> None:
    if "status" in fields and fields["status"] not in MENU_STATUSES:
        raise DbError(f"Unknown menu status {fields['status']!r}")
    with _db.transaction(conn):
        conn.execute(
            f"UPDATE menus SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), menu_id)
        )
