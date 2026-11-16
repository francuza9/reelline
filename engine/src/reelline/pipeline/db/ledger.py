"""The llm_calls cost ledger: recording calls, the cost context, manual recording."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from reelline.config import conf
from reelline.pipeline import db as _db

_COST_CONTEXT: ContextVar[dict | None] = ContextVar("cost_context", default=None)


@contextmanager
def cost_context(**fields) -> Iterator[None]:
    """Defaults for every llm_calls row recorded inside: job_id (a worker job), manual
    (a dev CLI). Nests; an explicit argument to record_llm_call() wins."""
    token = _COST_CONTEXT.set({**(_COST_CONTEXT.get() or {}), **fields})
    try:
        yield
    finally:
        _COST_CONTEXT.reset(token)


def record_llm_call(
    conn: sqlite3.Connection,
    purpose: str,
    model: str,
    prompt_version: int,
    items: int,
    usage: dict,
    at: str | None = None,
    script_id: int | None = None,
    cost_usd: float | None = None,
    served_model: str | None = None,
    *,
    reel_id: int | None = None,
    pick_id: int | None = None,
    job_id: int | None = None,
    manual: bool | None = None,
    estimated: str | None = None,
    error: str | None = None,
    attempt: int | None = None,
) -> int:
    """usage: {"input", "output", "thinking"} tokens; "output" excludes thinking. The
    pick defaults to the script's; job_id and manual to cost_context()'s. error: a failed
    try (reelline/providers/retry.py), attempt: its try number (or the try that answered)."""
    ctx = _COST_CONTEXT.get() or {}
    job_id = job_id if job_id is not None else ctx.get("job_id")
    manual = bool(ctx.get("manual", False) if manual is None else manual)
    with _db.transaction(conn):
        if pick_id is None and script_id is not None:
            row = conn.execute("SELECT pick_id FROM scripts WHERE id = ?", (script_id,)).fetchone()
            pick_id = row[0] if row else None
        cur = conn.execute(
            """INSERT INTO llm_calls (purpose, model, prompt_version, called_at, items, input_tokens,
                   output_tokens, thinking_tokens, script_id, cost_usd, served_model, reel_id, pick_id, job_id,
                   manual, estimated, error, attempt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                purpose,
                model,
                prompt_version,
                at or _db.now_iso(),
                items,
                usage.get("input"),
                usage.get("output"),
                usage.get("thinking"),
                script_id,
                cost_usd,
                served_model or model,
                reel_id,
                pick_id,
                job_id,
                int(manual),
                estimated,
                error,
                attempt,
            ),
        )
    return cur.lastrowid


def record_calls(
    conn: sqlite3.Connection,
    calls: list[dict],
    *,
    script_id: int | None = None,
    reel_id: int | None = None,
    pick_id: int | None = None,
    manual: bool | None = None,
) -> None:
    """Paid-call rows made away from the database (reelline/media/images/vision.py call_row(),
    reelline/media/tts.py call_row(): purpose, model, prompt_version, usage, cost_usd and the
    call's time "at") into llm_calls. A row a live recorder already wrote (live_recorder():
    "recorded_id") is updated instead: its purpose (a re-roll take) and whatever it
    now belongs to (the reel, once it exists)."""
    for c in calls:
        if c.get("recorded_id"):
            with _db.transaction(conn):
                conn.execute(
                    """UPDATE llm_calls SET purpose = ?, reel_id = COALESCE(?, reel_id),
                           script_id = COALESCE(?, script_id), pick_id = COALESCE(?, pick_id) WHERE id = ?""",
                    (c["purpose"], reel_id, script_id, pick_id, c["recorded_id"]),
                )
            continue
        record_llm_call(
            conn,
            c["purpose"],
            c["model"],
            c["prompt_version"],
            1,
            c["usage"],
            at=c.get("at"),
            script_id=script_id,
            cost_usd=c["cost_usd"],
            reel_id=reel_id,
            pick_id=pick_id,
            manual=manual,
        )


def live_recorder(
    conn: sqlite3.Connection, *, script_id: int | None = None, reel_id: int | None = None, manual: bool | None = None
) -> Callable[[dict], None]:
    """A reelline/providers/costs.py recorder: each cost row into llm_calls the moment its call
    returns (with what's known now: the script, the job and manual from cost_context()),
    marked "recorded_id" so record_calls() later only adds the reel."""

    def record(row: dict) -> None:
        row["recorded_id"] = record_llm_call(
            conn,
            row["purpose"],
            row["model"],
            row["prompt_version"],
            1,
            row["usage"],
            at=row.get("at"),
            script_id=script_id,
            cost_usd=row["cost_usd"],
            reel_id=reel_id,
            manual=manual,
        )

    return record


@contextmanager
def manual_recording(script_name: str | None = None, db_path: Path | None = None) -> Iterator[None]:
    """A dev CLI's paid calls written as they happen, tagged manual (record_manual() at
    the end then has nothing left to insert). No database: nothing recorded live, a warning."""
    from reelline.providers.costs import recording

    try:
        conn = _db.connect(db_path or conf().storage.db_path)
        script = None
        if script_name:
            script = conn.execute(
                "SELECT id FROM scripts WHERE path LIKE ? ORDER BY id DESC LIMIT 1", (f"%/{script_name}.txt",)
            ).fetchone()
    except Exception as exc:
        print(f"WARNING: costs not written as they happen: {exc}", flush=True)
        yield
        return
    try:
        with recording(live_recorder(conn, script_id=script["id"] if script else None, manual=True)):
            yield
    finally:
        conn.close()


def record_manual(
    calls: list[dict],
    *,
    script_name: str | None = None,
    db_path: Path | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """A dev CLI's paid calls (scripts/*.py), tagged manual, with the script whose file
    stem is `script_name` when there is one. Never raises: the calls are paid already, so
    a failure here is a loud warning, not a lost render."""
    if not calls:
        return
    if all(c.get("recorded_id") for c in calls):
        log(
            f"{len(calls)} paid call(s), ${sum(c['cost_usd'] or 0 for c in calls):.4f} (manual), recorded as they happened"
        )
        return
    try:
        conn = _db.connect(db_path or conf().storage.db_path)
        try:
            script = None
            if script_name:
                script = conn.execute(
                    "SELECT id FROM scripts WHERE path LIKE ? ORDER BY id DESC LIMIT 1", (f"%/{script_name}.txt",)
                ).fetchone()
            record_calls(conn, calls, script_id=script["id"] if script else None, manual=True)
        finally:
            conn.close()
    except Exception as exc:
        total = sum(c["cost_usd"] or 0 for c in calls)
        log(f"WARNING: {len(calls)} paid call(s), ${total:.4f}, NOT recorded in llm_calls: {exc}")
        return
    log(f"Recorded {len(calls)} paid call(s), ${sum(c['cost_usd'] or 0 for c in calls):.4f} (manual) in llm_calls")
