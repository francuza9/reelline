"""The jokes, flirt, details and share memories that feed the punch-up prompt."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from reelline.config import conf
from reelline.media.script_markup import (
    MarkupError,
    parse,
)
from reelline.pipeline import db
from reelline.pipeline import script as _s
from reelline.pipeline.script.base import NO_RECENT_DETAILS, NO_RECENT_FLIRTS, NO_RECENT_LINES, NO_RECENT_SHARES
from reelline.pipeline.script.flirt import has_banned_phrase
from reelline.pipeline.script.lock import details_line, flirt_line, share_line


def save_jokes(conn: sqlite3.Connection, script_id: int, report: dict) -> list[str]:
    """Store the final version's inserted sentences as the script's jokes."""
    jokes = [j for j in report["inserted"] if j.strip()]
    with db.transaction(conn):
        conn.execute("DELETE FROM jokes WHERE script_id = ?", (script_id,))
        at = db.now_iso()
        for joke in jokes:
            conn.execute("INSERT INTO jokes (script_id, text, created_at) VALUES (?, ?, ?)", (script_id, joke, at))
    return jokes


def recent_jokes(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    since = db.now_iso((now or datetime.now(UTC)) - timedelta(days=conf().steps.script.recent_days))
    statuses = conf().steps.script.recent_statuses
    rows = conn.execute(
        f"""SELECT j.text FROM jokes j JOIN scripts s ON s.id = j.script_id
            WHERE s.status IN ({", ".join("?" * len(statuses))}) AND j.created_at >= ?
            ORDER BY j.created_at DESC, j.id DESC""",
        (*statuses, since),
    ).fetchall()
    out: list[str] = []
    for (text,) in rows:
        if text not in out:
            out.append(text)
        if len(out) == conf().steps.script.recent_max:
            break
    return out


def _recent(conn: sqlite3.Connection, marker: str, line_of, most: int) -> list[str]:
    """The `line_of` lines of the latest scripts (steps.script.recent_statuses) whose final has
    `marker`, newest first, at most `most`, each once."""
    statuses = conf().steps.script.recent_statuses
    out: list[str] = []
    for row in conn.execute(
        f"""SELECT final FROM scripts WHERE final LIKE '%{marker}%'
                                AND status IN ({", ".join("?" * len(statuses))}) ORDER BY id DESC""",
        statuses,
    ):
        try:
            line = line_of(parse(row["final"]))
        except MarkupError:
            continue
        if line and line not in out:
            out.append(line)
        if len(out) == most:
            break
    return out


def recent_flirts(conn: sqlite3.Connection) -> list[str]:
    """The flirt lines of the latest scripts (steps.script.recent_statuses), newest first, at
    most steps.script.recent_flirts, each once."""
    return _recent(conn, "@flirt", flirt_line, conf().steps.script.recent_flirts)


def banned_phrase_ok(conn: sqlite3.Connection) -> bool:
    """Whether the next flirt line may use a banned phrase: none of the last
    flirt_banned_every - 1 stored flirt lines did."""
    return not any(
        has_banned_phrase(line) for line in _s.recent_flirts(conn)[: conf().steps.script.flirt_banned_every - 1]
    )


def recent_flirt_lines(conn: sqlite3.Connection) -> str:
    """{{RECENT_FLIRTS}}: the recent flirt lines as a plain list, or NO_RECENT_FLIRTS."""
    lines = _s.recent_flirts(conn)
    return "\n".join(f"- {line}" for line in lines) if lines else NO_RECENT_FLIRTS


def recent_details(conn: sqlite3.Connection) -> list[str]:
    """The details lines of the latest scripts (steps.script.recent_statuses), newest first, at
    most steps.script.recent_details, each once."""
    return _recent(conn, "@details", details_line, conf().steps.script.recent_details)


def details_block(lines: list[str] | tuple[str, ...]) -> str:
    """{{RECENT_DETAILS}}: the recent details lines as a plain list, or NO_RECENT_DETAILS."""
    return "\n".join(f"- {line}" for line in lines) if lines else NO_RECENT_DETAILS


def recent_shares(conn: sqlite3.Connection) -> list[str]:
    """The share lines of the latest scripts (steps.script.recent_statuses), newest first, at
    most steps.script.recent_shares, each once."""
    return _recent(conn, "@share", share_line, conf().steps.script.recent_shares)


def shares_block(lines: list[str] | tuple[str, ...]) -> str:
    """{{RECENT_SHARES}}: the recent share lines as a plain list, or NO_RECENT_SHARES."""
    return "\n".join(f"- {line}" for line in lines) if lines else NO_RECENT_SHARES


def recent_lines(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    """{{RECENT_LINES}}: the recent jokes as a plain list, or NO_RECENT_LINES."""
    jokes = recent_jokes(conn, now)
    return "\n".join(f"- {j}" for j in jokes) if jokes else NO_RECENT_LINES


# ---- The chain -------------------------------------------------------------------------
