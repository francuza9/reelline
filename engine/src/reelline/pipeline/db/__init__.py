"""The pipeline's state database: SQLite at the channel's data/ directory (stdlib sqlite3, no ORM).

connect() opens the file and runs every pending migration before returning. The
schema version lives in schema_version (one row per applied migration); MIGRATIONS
is the ordered list, and each migration runs in its own transaction together with
its schema_version row, so a failed migration leaves the database as it was. A
database newer than this code (a version past the last migration) fails loudly.

Later steps (picks, scripts, reels, posts) add their tables by appending a migration
to MIGRATIONS; never edit one that has shipped.

Connections run in autocommit mode and every write goes through transaction(), which
joins an outer transaction when there is one, so a caller can group several writes
(a story and its source's state) into one commit.

Tables (migration 1; migration 2 adds stories.date_source; 3 adds assessments and
llm_calls for pick.py; 4 adds picks and menus; 5 adds briefs; 6 adds
assessments.policy_angle; 7 adds scripts, jokes and captions; 8 adds reels; 9 adds
the reels' QA columns; 10 adds reviews, jobs, rejections, bot_state and the reels'
approval columns; 11 makes llm_calls the cost ledger: reel, pick, job, manual, estimated;
12 adds publications, publish_calls, jobs.detail and the reels' publish columns; 13
adds credits; 14 makes jobs.pick_id nullable and adds chains, daily_tasks, daily_events; 15 adds
post_stats, account_stats, stats_health and publications.native_id; 16 reels.opening_version; 17
chains.mode; 18 adds dismissals and the pick_feedback view; 19 jobs.step_at; ...; 28 the reels'
cold open: gag, gag_s, gag_mode):
  sources         runtime state per source (the registry is sources.yaml)
  stories         one row per ingested story; canonical_url is unique
  dropped         URLs ingest looked at and dropped (too short, before the first
                  run's window, robots, ...), so later runs skip them without a fetch
  runs            one row per ingest run: start, end, per-source counts and errors

Timestamps are ISO 8601 UTC strings with seconds ("2026-09-30T21:05:00+00:00"), so
they compare as strings.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from reelline.config import conf
from reelline.pipeline.db.assessments import (
    ASSESSMENT_FIELDS,
    TECH_RELEASED,
    archive_source_stories,
    conn_recent_picks,
    get_story,
    release_tech_stories,
    save_assessment,
    stale_story_ids,
    tech_assessed_since,
    tech_candidate_rows,
    unassessed_story_ids,
)
from reelline.pipeline.db.base import (
    CHAIN_MODES,
    CHAIN_STATUSES,
    CHOSEN_BY,
    DISMISS_REASONS,
    JOB_STATUSES,
    MENU_STATUSES,
    PICK_STATUSES,
    PUBLICATION_STATES,
    REEL_STATUSES,
    REVIEW_STATUSES,
    RUN_STATUSES,
    SCRIPT_STATUSES,
    STORY_STATUSES,
    DbError,
    Migration,
)
from reelline.pipeline.db.ledger import (
    cost_context,
    live_recorder,
    manual_recording,
    record_calls,
    record_llm_call,
    record_manual,
)
from reelline.pipeline.db.menus import (
    add_menu,
    dismiss_story,
    dismissed,
    get_menu,
    get_state,
    open_menus,
    set_state,
    update_menu,
)
from reelline.pipeline.db.migrations_a import MIGRATIONS_A
from reelline.pipeline.db.migrations_b import MIGRATIONS_B
from reelline.pipeline.db.migrations_c import MIGRATIONS_C
from reelline.pipeline.db.picks import (
    add_pick,
    next_queue_position,
    queued_picks,
    slot_pick,
    update_pick,
)
from reelline.pipeline.db.sources import (
    all_sources,
    disable_source,
    ensure_source,
    get_source,
    record_source_failure,
    record_source_ok,
    reset_source,
)
from reelline.pipeline.db.stories import (
    NewStory,
    finish_run,
    insert_story,
    known_hash,
    known_url,
    last_runs,
    record_dropped,
    set_story_status,
    start_run,
    story_counts_by_source,
    story_counts_by_status_topic,
)

__all__ = [
    "ASSESSMENT_FIELDS",
    "CHAIN_MODES",
    "CHAIN_STATUSES",
    "CHOSEN_BY",
    "DISMISS_REASONS",
    "JOB_STATUSES",
    "MENU_STATUSES",
    "MIGRATIONS",
    "PICK_STATUSES",
    "PUBLICATION_STATES",
    "REEL_STATUSES",
    "REVIEW_STATUSES",
    "RUN_STATUSES",
    "SCRIPT_STATUSES",
    "STORY_STATUSES",
    "TECH_RELEASED",
    "_COST_CONTEXT",
    "DbError",
    "Migration",
    "NewStory",
    "add_menu",
    "add_pick",
    "all_sources",
    "archive_source_stories",
    "conn_recent_picks",
    "connect",
    "cost_context",
    "disable_source",
    "dismiss_story",
    "dismissed",
    "ensure_source",
    "finish_run",
    "get_menu",
    "get_source",
    "get_state",
    "get_story",
    "insert_story",
    "known_hash",
    "known_url",
    "last_runs",
    "live_recorder",
    "manual_recording",
    "migrate",
    "next_queue_position",
    "now_iso",
    "open_menus",
    "queued_picks",
    "record_calls",
    "record_dropped",
    "record_llm_call",
    "record_manual",
    "record_source_failure",
    "record_source_ok",
    "release_tech_stories",
    "reset_source",
    "save_assessment",
    "schema_version",
    "set_state",
    "set_story_status",
    "slot_pick",
    "stale_story_ids",
    "start_run",
    "story_counts_by_source",
    "story_counts_by_status_topic",
    "tech_assessed_since",
    "tech_candidate_rows",
    "transaction",
    "unassessed_story_ids",
    "update_menu",
    "update_pick",
]

MIGRATIONS: tuple[Migration, ...] = MIGRATIONS_A + MIGRATIONS_B + MIGRATIONS_C


def _check_migrations(migrations: tuple[Migration, ...]) -> None:
    versions = [m.version for m in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise DbError(f"Migrations must be numbered 1..n in order, got {versions}")


def schema_version(conn: sqlite3.Connection) -> int:
    """The highest applied migration, 0 for a fresh database."""
    row = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'").fetchone()
    if row is None:
        return 0
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, migrations: tuple[Migration, ...] = MIGRATIONS) -> list[int]:
    """Apply every pending migration in order; the versions applied."""
    _check_migrations(migrations)
    with transaction(conn):
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )"""
        )
    current = schema_version(conn)
    latest = migrations[-1].version if migrations else 0
    if current > latest:
        raise DbError(f"Database schema is version {current}, newer than this code knows (latest {latest})")
    applied = []
    for m in migrations:
        if m.version <= current:
            continue
        if m.rebuild:
            conn.execute("PRAGMA foreign_keys = OFF")  # a no-op inside a transaction: set before it
        try:
            with transaction(conn):
                for statement in m.statements:
                    conn.execute(statement)
                if m.rebuild:
                    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
                    if broken:
                        raise sqlite3.IntegrityError(
                            f"foreign_key_check after the rebuild: {[tuple(r) for r in broken][:5]}"
                        )
                conn.execute(
                    "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                    (m.version, m.name, now_iso()),
                )
        except sqlite3.Error as exc:
            raise DbError(f"Migration {m.version} ({m.name}) failed: {exc}") from exc
        finally:
            if m.rebuild:
                conn.execute("PRAGMA foreign_keys = ON")
        applied.append(m.version)
    return applied


# ---- Connection and transactions -------------------------------------------------------


def connect(path: Path | None = None, migrations: tuple[Migration, ...] = MIGRATIONS) -> sqlite3.Connection:
    """Open (creating if needed) the database and bring its schema up to date."""
    path = path or conf().storage.db_path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path, isolation_level=None, timeout=conf().storage.db_busy_timeout_s)
    except sqlite3.Error as exc:
        raise DbError(f"Can't open {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        migrate(conn, migrations)
    except Exception:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on any exception. Inside an open
    transaction it joins that one (the outer block commits or rolls back)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def now_iso(at: datetime | None = None) -> str:
    at = at or datetime.now(UTC)
    if at.tzinfo is None:
        raise DbError(f"Naive datetime {at!r}: timestamps must be timezone-aware")
    return at.astimezone(UTC).isoformat(timespec="seconds")
