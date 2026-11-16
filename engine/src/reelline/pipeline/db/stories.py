"""Ingested stories, dropped URLs and ingest runs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from reelline.pipeline import db as _db
from reelline.pipeline.db.base import RUN_STATUSES, STORY_STATUSES, DbError
from reelline.pipeline.db.sources import ensure_source


@dataclass(frozen=True)
class NewStory:
    source_id: str
    url: str
    canonical_url: str
    title: str
    published_at: str | None
    fetched_at: str
    lang: str
    text: str
    text_hash: str
    word_count: int
    topic_hint: str
    run_id: int | None = None
    date_source: str | None = None
    status: str = "new"  # or "archived" (a source with assess: false)


def known_url(conn: sqlite3.Connection, *urls: str) -> str | None:
    """Where any of the URLs is already recorded: "story", "dropped" or None."""
    for url in dict.fromkeys(u for u in urls if u):
        if conn.execute("SELECT 1 FROM stories WHERE canonical_url = ? OR url = ? LIMIT 1", (url, url)).fetchone():
            return "story"
        if conn.execute("SELECT 1 FROM dropped WHERE url = ? OR canonical_url = ? LIMIT 1", (url, url)).fetchone():
            return "dropped"
    return None


def known_hash(conn: sqlite3.Connection, text_hash: str) -> bool:
    return conn.execute("SELECT 1 FROM stories WHERE text_hash = ? LIMIT 1", (text_hash,)).fetchone() is not None


def insert_story(conn: sqlite3.Connection, story: NewStory) -> int:
    """Insert with status "new" (or "archived"); the new row's id. A known canonical URL fails."""
    if story.status not in ("new", "archived"):
        raise DbError(f"A new story is new or archived, not {story.status!r}")
    try:
        with _db.transaction(conn):
            ensure_source(conn, story.source_id)
            cur = conn.execute(
                """INSERT INTO stories (source_id, run_id, url, canonical_url, title, published_at,
                       date_source, fetched_at, lang, text, text_hash, word_count, status, topic_hint)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story.source_id,
                    story.run_id,
                    story.url,
                    story.canonical_url,
                    story.title,
                    story.published_at,
                    story.date_source,
                    story.fetched_at,
                    story.lang,
                    story.text,
                    story.text_hash,
                    story.word_count,
                    story.status,
                    story.topic_hint,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise DbError(f"Can't insert story {story.canonical_url}: {exc}") from exc
    return cur.lastrowid


def record_dropped(
    conn: sqlite3.Connection,
    source_id: str,
    url: str,
    canonical_url: str | None,
    reason: str,
    detail: str | None = None,
    at: str | None = None,
) -> None:
    """Remember a URL ingest dropped, so the next run skips it without fetching."""
    with _db.transaction(conn):
        ensure_source(conn, source_id)
        conn.execute(
            """INSERT OR REPLACE INTO dropped (url, canonical_url, source_id, reason, detail, dropped_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (url, canonical_url if canonical_url != url else None, source_id, reason, detail, at or _db.now_iso()),
        )


def set_story_status(conn: sqlite3.Connection, story_id: int, status: str, reason: str | None = None) -> None:
    if status not in STORY_STATUSES:
        raise DbError(f"Unknown story status {status!r} (one of {', '.join(STORY_STATUSES)})")
    with _db.transaction(conn):
        cur = conn.execute("UPDATE stories SET status = ?, status_reason = ? WHERE id = ?", (status, reason, story_id))
        if cur.rowcount != 1:
            raise DbError(f"No story with id {story_id}")


# ---- Runs ------------------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, args: dict, at: str | None = None) -> int:
    with _db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO runs (started_at, status, args) VALUES (?, 'running', ?)",
            (at or _db.now_iso(), json.dumps(args, sort_keys=True)),
        )
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection, run_id: int, status: str, counts: dict, errors: dict, at: str | None = None
) -> None:
    if status not in RUN_STATUSES or status == "running":
        raise DbError(f"Bad final run status {status!r}")
    with _db.transaction(conn):
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, counts = ?, errors = ? WHERE id = ?",
            (
                at or _db.now_iso(),
                status,
                json.dumps(counts, sort_keys=True, ensure_ascii=False),
                json.dumps(errors, sort_keys=True, ensure_ascii=False),
                run_id,
            ),
        )


# ---- Reports ---------------------------------------------------------------------------


def story_counts_by_source(conn: sqlite3.Connection, since: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT source_id, COUNT(*) FROM stories WHERE fetched_at >= ? GROUP BY source_id", (since,)
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def story_counts_by_status_topic(conn: sqlite3.Connection, since: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT status, topic_hint, COUNT(*) AS n FROM stories WHERE fetched_at >= ?
           GROUP BY status, topic_hint ORDER BY status, topic_hint""",
        (since,),
    ).fetchall()


def last_runs(conn: sqlite3.Connection, n: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
