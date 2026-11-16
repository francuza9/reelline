"""The slot's pick and script, and the reels table's writes."""

from __future__ import annotations

import sqlite3

from reelline.config import conf
from reelline.paths import stored_path
from reelline.pipeline import db
from reelline.pipeline.produce.base import ProduceError


def slot_pick(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> sqlite3.Row:
    pick = db.slot_pick(conn, slot_date, slot_time)
    if pick is None:
        raise ProduceError(f"No pick for {slot_date} {slot_time}")
    if pick["status"] not in conf().steps.produce.pick_statuses:
        done = conn.execute(
            "SELECT id FROM reels WHERE pick_id = ? AND status = 'rendered' ORDER BY id DESC LIMIT 1", (pick["id"],)
        ).fetchone()
        extra = f" (reel #{done['id']})" if done and pick["status"] == "rendered" else ""
        raise ProduceError(
            f"Pick #{pick['id']} for {slot_date} {slot_time} is {pick['status']}{extra}; produce takes "
            f"a pick that is {' or '.join(conf().steps.produce.pick_statuses)}"
        )
    return pick


def latest_script(conn: sqlite3.Connection, pick_id: int) -> sqlite3.Row:
    marks = ", ".join("?" * len(conf().steps.produce.script_statuses))
    row = conn.execute(
        f"""SELECT * FROM scripts WHERE pick_id = ? AND status IN ({marks}) AND path IS NOT NULL
                           ORDER BY id DESC LIMIT 1""",
        (pick_id, *conf().steps.produce.script_statuses),
    ).fetchone()
    if row is None:
        raise ProduceError(
            f"Pick #{pick_id} has no script that is {' or '.join(conf().steps.produce.script_statuses)} "
            "with a file (python -m reelline.pipeline.script run)"
        )
    path = stored_path(row["path"])
    if not path.is_file():
        raise ProduceError(f"Script #{row['id']}'s file is missing: {path}")
    return row


def supersede(conn: sqlite3.Connection, pick_id: int, reel_id: int) -> None:
    """Earlier rendered reels of the pick become superseded by `reel_id`."""
    with db.transaction(conn):
        conn.execute(
            "UPDATE reels SET status = 'superseded', updated_at = ? WHERE pick_id = ? AND id < ? "
            "AND status = 'rendered'",
            (db.now_iso(), pick_id, reel_id),
        )


def insert_reel(conn: sqlite3.Connection, **fields) -> int:
    cols = ", ".join(fields)
    with db.transaction(conn):
        return conn.execute(
            f"INSERT INTO reels ({cols}) VALUES ({', '.join('?' * len(fields))})", tuple(fields.values())
        ).lastrowid


def update_reel(conn: sqlite3.Connection, reel_id: int, **fields) -> None:
    fields["updated_at"] = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            f"UPDATE reels SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), reel_id)
        )
