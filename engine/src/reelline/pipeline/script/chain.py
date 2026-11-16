"""write_script(): the chain for a pick, stored in the scripts table."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from reelline.config import conf
from reelline.media import style as reel_style
from reelline.pipeline import db
from reelline.pipeline import script as _s
from reelline.pipeline.script.base import ScriptError
from reelline.pipeline.script.flirt import pick_flirt
from reelline.pipeline.script.recent import save_jokes
from reelline.pipeline.script.runs import ScriptRun, _brief_for, _update, check_pick, unfinished
from reelline.pipeline.script.session import CLAUDE_ROLES, PassesStore, _versions, session_steps, unchecked
from reelline.pipeline.script.story import hook_examples
from reelline.providers import TransportFailure


def write_script(
    conn: sqlite3.Connection,
    pick_id: int,
    *,
    claude,
    gemini,
    source_names: dict[str, str],
    log=lambda m: print(m, flush=True),
    steps: list | None = None,
) -> ScriptRun:
    """The pick's script: its session's steps (steps; default: the configured chain's write ... fx,
    reelline/steps/) run by reelline/pipeline/runner.py over this script's row, each pass stored in
    scripts.passes as it's done (PassesStore), the fact-check's repair round included."""
    from reelline.pipeline.endcard import CardError
    from reelline.pipeline.factcheck import FactCheckError, failure_text
    from reelline.pipeline.runner import Runner
    from reelline.pipeline.step import Artifact, Context

    pick = conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone()
    check_pick(pick, pick_id)
    story = db.get_story(conn, pick["story_id"])
    brief_row = _brief_for(conn, pick, gemini, source_names, log)
    brief = brief_row["markdown"]
    earlier = unfinished(conn, pick_id, brief_row["id"])
    if earlier is not None:
        script_id, attempt = earlier["id"], earlier["attempt"]
        passes = json.loads(earlier["passes"] or "{}")
        _update(conn, script_id, status="running", error=None)
        log(
            f"Script #{script_id} (pick #{pick_id}, attempt {attempt}) goes on from its {earlier['status']} run; "
            f"passes done: {', '.join(passes) or 'none'}"
        )
    else:
        attempt = conn.execute("SELECT COUNT(*) FROM scripts WHERE pick_id = ?", (pick_id,)).fetchone()[0] + 1
        now = db.now_iso()
        with db.transaction(conn):
            script_id = conn.execute(
                """INSERT INTO scripts (pick_id, story_id, brief_id, attempt, status, model, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'running', ?, ?, ?)""",
                (pick_id, story["id"], brief_row["id"], attempt, conf().models.writer.model, now, now),
            ).lastrowid
        passes = {}
        log(f"Script #{script_id} (pick #{pick_id}, attempt {attempt})")
    style = reel_style.current()
    log(
        f"  style: {style.name}"
        + ("" if style.jokes else f" ({style.punchup_prompt.name}: no jokes, no recent lines or previous opening fed)")
    )
    previous = _s.previous_opening(conn, script_id) if style.jokes else None
    publish_date = date.fromisoformat(pick["slot_date"]) if pick["slot_date"] else None
    flirt, angle = pick_flirt(conn, pick)
    log(f"  flirt line: {'yes (angle: ' + angle + ')' if flirt else 'no'}")
    examples, examples_note = hook_examples()
    log(f"  {examples_note}")

    ctx = Context(
        artifacts={Artifact.STORY: story, Artifact.BRIEF: brief},
        store=PassesStore(conn, script_id, passes),
        log=log,
        conn=conn,
        clients={**{role: claude for role in CLAUDE_ROLES}, "checker": gemini},
        state={
            "script_id": script_id,
            "publish_date": publish_date,
            "flirt": flirt,
            "angle": angle,
            "previous": previous,
            "examples": examples,
        },
    )
    try:
        outcome = Runner(steps if steps is not None else session_steps(), log=log).run(ctx)
        if outcome.status != "ok":
            raise ScriptError(f"{outcome.stopped_at}: {outcome.detail}")
        versions = _versions(ctx) or [unchecked(ctx)]
        repair_error = outcome.repair_error
        card, fx = ctx.artifacts.get(Artifact.CARDS), ctx.artifacts.get(Artifact.GRAPHICS)
    except CardError as exc:
        final = (_versions(ctx) or [unchecked(ctx)])[-1]
        _update(
            conn, script_id, status="failed", error=f"end card: {exc}", draft=final.story.text, final=final.punchup.text
        )
        raise ScriptError(f"script #{script_id} failed: end card: {exc}") from None
    except (ScriptError, FactCheckError, TransportFailure) as exc:
        versions = _versions(ctx)
        _update(
            conn,
            script_id,
            status="failed",
            error=str(exc),
            **({"draft": versions[-1].story.text, "final": versions[-1].punchup.text} if versions else {}),
        )
        if isinstance(exc, TransportFailure):  # names the call itself, for the job's report
            raise
        raise ScriptError(f"script #{script_id} failed: {exc}") from None

    final = versions[-1]
    status = "ok" if final.factcheck.passed else "flagged"
    rounds = [{"route": v.route, **v.factcheck.to_json()} for v in versions]
    _update(
        conn,
        script_id,
        status=status,
        draft=final.story.text,
        final=final.punchup.text,
        draft_lock=json.dumps(final.report, ensure_ascii=False),
        factcheck=json.dumps({"rounds": rounds, "repair_error": repair_error}, ensure_ascii=False),
        failures=json.dumps([failure_text(c) for c in final.factcheck.failures], ensure_ascii=False),
        error=repair_error,
    )
    save_jokes(conn, script_id, final.report)
    log(f"Script #{script_id}: {status}")
    return ScriptRun(script_id, status, versions, brief_row["id"], repair_error, card, fx)
