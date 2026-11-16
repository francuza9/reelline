"""Assessments, the tech slot's candidates and stale-story queries."""

from __future__ import annotations

import json
import sqlite3

from reelline.pipeline import db as _db

ASSESSMENT_FIELDS = (
    "banned",
    "banned_reason",
    "topic",
    "edu_type",
    "provider",
    "provider_tier",
    "cost",
    "marketing_or_low_credibility",
    "georgia_relevance",
    "deadline",
    "who_can_apply",
    "big_news",
    "niche",
    "base_score",
    "same_program_key",
    "summary",
    "policy_angle",
    "opportunity",
    "open_status",
    "reach",
    "novelty",
    "event_date",
    "hook_strength",
    "tech_significance",
)


def save_assessment(
    conn: sqlite3.Connection,
    story_id: int,
    fields: dict,
    raw: dict,
    model: str,
    prompt_version: int,
    call_id: int | None,
    at: str | None = None,
) -> None:
    """Insert or replace (a --force re-assessment) one story's assessment."""
    with _db.transaction(conn):
        conn.execute(
            f"""INSERT OR REPLACE INTO assessments (story_id, call_id, model, prompt_version, assessed_at,
                   {", ".join(ASSESSMENT_FIELDS)}, raw_json)
               VALUES (?, ?, ?, ?, ?, {", ".join("?" * len(ASSESSMENT_FIELDS))}, ?)""",
            (
                story_id,
                call_id,
                model,
                prompt_version,
                at or _db.now_iso(),
                *(fields.get(k) for k in ASSESSMENT_FIELDS),
                json.dumps(raw, ensure_ascii=False, sort_keys=True),
            ),
        )


def unassessed_story_ids(conn: sqlite3.Connection) -> list[int]:
    """Stories with no assessment yet; archived ones are never assessed."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT s.id FROM stories s LEFT JOIN assessments a ON a.story_id = s.id "
            "WHERE a.story_id IS NULL AND s.status != 'archived' ORDER BY s.id"
        )
    ]


def archive_source_stories(conn: sqlite3.Connection, source_id: str) -> int:
    """A source with assess: false: its stories still "new" (never picked) become archived,
    so an assessment from before can't make them eligible. How many."""
    with _db.transaction(conn):
        return conn.execute(
            "UPDATE stories SET status = 'archived', status_reason = 'source assess: false' "
            "WHERE source_id = ? AND status = 'new' AND COALESCE(status_reason, '') != ?",
            (source_id, TECH_RELEASED),
        ).rowcount


TECH_RELEASED = "tech candidate, assessed"  # status_reason of an archived story released for the tech slot


def tech_candidate_rows(conn: sqlite3.Connection, sources: tuple[str, ...], since: str) -> list[sqlite3.Row]:
    """Archived, unassessed stories from the tech candidate sources fetched at or after `since`."""
    marks = ", ".join("?" * len(sources))
    return conn.execute(
        f"SELECT s.* FROM stories s LEFT JOIN assessments a ON a.story_id = s.id WHERE a.story_id IS NULL "
        f"AND s.status = 'archived' AND s.source_id IN ({marks}) AND s.fetched_at >= ? ORDER BY s.id",
        (*sources, since),
    ).fetchall()


def tech_assessed_since(conn: sqlite3.Connection, sources: tuple[str, ...], since: str) -> int:
    """Tech candidates (from the candidate sources) assessed at or after `since`."""
    marks = ", ".join("?" * len(sources))
    return conn.execute(
        f"SELECT COUNT(*) FROM assessments a JOIN stories s ON s.id = a.story_id "
        f"WHERE s.source_id IN ({marks}) AND a.assessed_at >= ?",
        (*sources, since),
    ).fetchone()[0]


def conn_recent_picks(conn: sqlite3.Connection, since: str) -> list[sqlite3.Row]:
    """Title and URL of the stories picked (any live status) since `since`."""
    return conn.execute(
        "SELECT s.title, s.canonical_url FROM picks p JOIN stories s ON s.id = p.story_id "
        "WHERE p.created_at >= ? AND p.status NOT IN ('rejected', 'skipped')",
        (since,),
    ).fetchall()


def release_tech_stories(conn: sqlite3.Connection, story_ids: list[int]) -> None:
    """Assessed tech candidates leave the archive (status new), so they can be ranked and picked."""
    with _db.transaction(conn):
        conn.executemany(
            "UPDATE stories SET status = 'new', status_reason = ? WHERE id = ? AND status = 'archived'",
            [(TECH_RELEASED, i) for i in story_ids],
        )


def stale_story_ids(conn: sqlite3.Connection, version: int, since: str, sources: set[str] | None = None) -> list[int]:
    """Stories fetched at or after `since` (from `sources` only, when given) whose
    assessment is from a prompt version before `version`; never an archived one."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT s.id, s.source_id FROM stories s JOIN assessments a ON a.story_id = s.id "
            "WHERE a.prompt_version < ? AND s.fetched_at >= ? AND s.status != 'archived' ORDER BY s.id",
            (version, since),
        )
        if sources is None or r[1] in sources
    ]


def get_story(conn: sqlite3.Connection, story_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
