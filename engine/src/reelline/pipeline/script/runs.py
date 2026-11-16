"""The chain's stored state: Version, ScriptRun, the pick's brief, unfinished scripts, pass JSON."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from reelline.pipeline import db
from reelline.pipeline.script.base import PassResult, ScriptError


@dataclass
class Version:
    """One story + punch-up + fact-check round."""

    route: str  # "first", "punchup" (re-run) or "story+punchup" (re-run)
    story: PassResult
    punchup: PassResult
    report: dict
    factcheck: object  # reelline.pipeline.factcheck.FactCheck


@dataclass
class ScriptRun:
    script_id: int
    status: str
    versions: list[Version]
    brief_id: int
    repair_error: str | None = None
    card: object = None  # reelline.pipeline.endcard.Card, the end card's facts
    fx: object = None  # reelline.pipeline.fx.FxCheck, the fact graphics the render may show

    @property
    def final(self) -> Version:
        return self.versions[-1]


def _brief_for(conn: sqlite3.Connection, pick, gemini, source_names: dict[str, str], log) -> sqlite3.Row:
    from reelline.pipeline.brief import build_brief

    row = conn.execute("SELECT * FROM briefs WHERE pick_id = ? ORDER BY id DESC LIMIT 1", (pick["id"],)).fetchone()
    if row is None:
        log(f"No brief for pick #{pick['id']} yet; building it")
        build_brief(conn, gemini, pick["story_id"], source_names, pick["id"])
        row = conn.execute("SELECT * FROM briefs WHERE pick_id = ? ORDER BY id DESC LIMIT 1", (pick["id"],)).fetchone()
    return row


def check_pick(pick: sqlite3.Row | None, pick_id: int) -> None:
    """ScriptError unless the pick may get a script."""
    if pick is None:
        raise ScriptError(f"No pick #{pick_id}")
    if pick["status"] not in ("picked", "scripted"):
        raise ScriptError(
            f"Pick #{pick_id} is {pick['status']}; only picked (or scripted, for a new attempt) picks get a script"
        )


def stored_run(conn: sqlite3.Connection, pick_id: int) -> ScriptRun:
    """The pick's latest written script as a ScriptRun (its final text, card and fx from its row),
    for the steps after the script session run on their own (reelline run --from caption)."""
    from reelline.pipeline.endcard import Card
    from reelline.pipeline.fx import FxCheck

    row = conn.execute(
        "SELECT * FROM scripts WHERE pick_id = ? AND status IN ('ok', 'flagged') ORDER BY id DESC LIMIT 1", (pick_id,)
    ).fetchone()
    if row is None:
        raise ScriptError(f"Pick #{pick_id} has no written script")
    passes = json.loads(row["passes"] or "{}")
    text = PassResult(row["final"])
    version = Version("stored", PassResult(row["draft"] or ""), text, {}, None)
    return ScriptRun(
        row["id"],
        row["status"],
        [version],
        row["brief_id"],
        row["error"],
        Card.from_json(passes["card"]) if "card" in passes else None,
        FxCheck.from_json(passes["fx"]) if "fx" in passes else None,
    )


def _update(conn: sqlite3.Connection, script_id: int, **fields) -> None:
    fields["updated_at"] = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            f"UPDATE scripts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), script_id)
        )


def unfinished(conn: sqlite3.Connection, pick_id: int, brief_id: int) -> sqlite3.Row | None:
    """The pick's latest script if a retried or resumed job should go on with it: still
    running (its worker died), failed, or written but without its file (its caption or
    finish() failed), and on the same brief. None: write a new one."""
    row = conn.execute("SELECT * FROM scripts WHERE pick_id = ? ORDER BY id DESC LIMIT 1", (pick_id,)).fetchone()
    if row is None or row["brief_id"] != brief_id:
        return None
    if row["status"] in ("running", "failed") or (row["status"] in ("ok", "flagged") and row["path"] is None):
        return row
    return None


def _pass_json(result: PassResult) -> dict:
    """A pass as stored in scripts.passes."""
    return {
        "text": result.text,
        "retried": result.retried,
        **({"openings": result.openings} if result.openings is not None else {}),
        **({"fx_repaired": result.fx_repaired} if result.fx_repaired else {}),
    }
