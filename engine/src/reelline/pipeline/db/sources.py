"""Per-source runtime state (the registry itself is sources.yaml)."""

from __future__ import annotations

import sqlite3

from reelline.pipeline import db as _db
from reelline.pipeline.db.base import DbError


def ensure_source(conn: sqlite3.Connection, source_id: str) -> None:
    with _db.transaction(conn):
        conn.execute("INSERT OR IGNORE INTO sources (id) VALUES (?)", (source_id,))


def get_source(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def all_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM sources ORDER BY id").fetchall()


def record_source_ok(
    conn: sqlite3.Connection, source_id: str, etag: str | None, last_modified: str | None, at: str | None = None
) -> None:
    """A successful fetch: failures back to 0, the validators for the next conditional GET."""
    with _db.transaction(conn):
        ensure_source(conn, source_id)
        conn.execute(
            """UPDATE sources SET last_ok_at = ?, consecutive_failures = 0,
                   etag = ?, last_modified = ? WHERE id = ?""",
            (at or _db.now_iso(), etag, last_modified, source_id),
        )


def record_source_failure(
    conn: sqlite3.Connection, source_id: str, error: str, max_failures: int, at: str | None = None
) -> bool:
    """Count a failure; at max_failures in a row the source is disabled. True if it
    was disabled by this failure."""
    at = at or _db.now_iso()
    with _db.transaction(conn):
        ensure_source(conn, source_id)
        conn.execute(
            """UPDATE sources SET consecutive_failures = consecutive_failures + 1,
                   last_error = ?, last_error_at = ? WHERE id = ?""",
            (error, at, source_id),
        )
        row = get_source(conn, source_id)
        if row["disabled_reason"] is None and row["consecutive_failures"] >= max_failures:
            disable_source(conn, source_id, f"{row['consecutive_failures']} failures in a row, last: {error}", at)
            return True
    return False


def disable_source(conn: sqlite3.Connection, source_id: str, reason: str, at: str | None = None) -> None:
    if not reason:
        raise DbError(f"Disabling {source_id} needs a reason")
    with _db.transaction(conn):
        ensure_source(conn, source_id)
        conn.execute(
            "UPDATE sources SET disabled_reason = ?, disabled_at = ? WHERE id = ?",
            (reason, at or _db.now_iso(), source_id),
        )


def reset_source(conn: sqlite3.Connection, source_id: str) -> None:
    """Re-enable a source disabled in the database and clear its failure count."""
    with _db.transaction(conn):
        ensure_source(conn, source_id)
        conn.execute(
            """UPDATE sources SET disabled_reason = NULL, disabled_at = NULL,
                   consecutive_failures = 0, last_error = NULL, last_error_at = NULL
               WHERE id = ?""",
            (source_id,),
        )
