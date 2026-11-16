"""The script's files and review text: script file, end card file, fact-check, cost and fx summaries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from reelline.config import conf
from reelline.paths import REPO_ROOT
from reelline.pipeline import db
from reelline.pipeline.script.openings import _scored
from reelline.pipeline.script.runs import ScriptRun, _update


def script_file(conn: sqlite3.Connection, script_id: int) -> Path:
    from reelline.pipeline.brief import slugify

    row = conn.execute(
        """SELECT s.*, p.slot_date, p.slot_time, a.same_program_key FROM scripts s JOIN picks p ON p.id = s.pick_id
           LEFT JOIN assessments a ON a.story_id = s.story_id WHERE s.id = ?""",
        (script_id,),
    ).fetchone()
    slot = (row["slot_time"] or "manual").replace(":", "")
    return conf().storage.scripts_dir / f"{row['slot_date']}_{slot}_{slugify(row['same_program_key'] or 'story')}.txt"


def finish(conn: sqlite3.Connection, run: ScriptRun) -> Path:
    """Write the final script file, record its path, set the pick to scripted."""
    path = script_file(conn, run.script_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    final = run.final.punchup.text
    path.write_text(final if final.endswith("\n") else final + "\n", encoding="utf-8")
    if run.card is not None:
        write_card_file(path, run.script_id, run.card)
    if run.fx is not None:
        from reelline.pipeline.fx import write_file as write_fx_file

        write_fx_file(path, run.script_id, run.fx)
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    _update(conn, run.script_id, path=str(shown))
    pick_id = conn.execute("SELECT pick_id FROM scripts WHERE id = ?", (run.script_id,)).fetchone()[0]
    db.update_pick(conn, pick_id, status="scripted")
    return path


def write_card_file(script_path: Path, script_id: int, card) -> Path:
    """The end card's facts next to the script (reelline/media/outro.py facts_path()), what render_reel.py draws."""
    from reelline.media.outro import facts_path, load_outro, write_facts

    return write_facts(facts_path(script_path), card.lines, load_outro(), script_id=script_id, dropped=card.dropped)


def complete(conn: sqlite3.Connection, run: ScriptRun, claude) -> Path:
    """The caption fields (unless the script has them: a run that went on from a failed
    caption), then finish(): the pick is scripted only once both are there."""
    from reelline.pipeline.caption import write_caption

    if conn.execute("SELECT 1 FROM captions WHERE script_id = ?", (run.script_id,)).fetchone() is None:
        write_caption(conn, claude, run.script_id)
    return finish(conn, run)


def calls_for(conn: sqlite3.Connection, script_id: int) -> list[sqlite3.Row]:
    """The script's LLM calls, plus its brief's."""
    return conn.execute(
        """SELECT * FROM llm_calls WHERE script_id = ? OR id = (SELECT b.call_id FROM scripts s
               JOIN briefs b ON b.id = s.brief_id WHERE s.id = ?) ORDER BY id""",
        (script_id, script_id),
    ).fetchall()


def cost_lines(conn: sqlite3.Connection, script_id: int) -> list[str]:
    lines, total = [], 0.0
    for c in calls_for(conn, script_id):
        cost = c["cost_usd"]
        total += cost or 0
        lines.append(
            f"  {c['purpose']:<14} {c['served_model'] or c['model']:<18} in {c['input_tokens'] or 0:>6}  "
            f"out {c['output_tokens'] or 0:>5}  thinking {c['thinking_tokens'] or 0:>5}  "
            + (f"${cost:.4f}" if cost is not None else "cost unknown")
            + (f"  failed (try {c['attempt']}): {c['error'][:80]}" if c["error"] else "")
        )
    lines.append(f"  total ${total:.4f}")
    return lines


def factcheck_summary(row: sqlite3.Row) -> str:
    from reelline.pipeline.factcheck import DEFINITION

    fc = json.loads(row["factcheck"] or "{}")
    rounds = fc.get("rounds", [])
    out = []
    for n, r in enumerate(rounds, 1):
        c = r["counts"]
        out.append(
            f"Round {n} ({r['route']}): {c['supported']} supported, {c['unsupported']} unsupported, "
            f"{c['distorted']} distorted" + (f", {c[DEFINITION]} general definition" if c.get(DEFINITION) else "")
        )
    final = rounds[-1] if rounds else {"claims": []}
    for claim in final["claims"]:
        mark = {"supported": "OK", DEFINITION: "OK, general definition"}.get(claim["verdict"], claim["verdict"].upper())
        out.append(
            f"- [{mark}, {claim['origin']}] {claim['claim']}" + (f" ({claim['note']})" if claim.get("note") else "")
        )
    if "inserted" in final:  # scripts checked before prompt v2 have no review
        out.append("Inserted sentences:")
        for r in final["inserted"]:
            kind = "news, checked" if r["news_assertion"] else "exempt"
            out.append(f"- S{r['sentence']} [{kind}] {r['reason']}")
    if fc.get("repair_error"):
        out.append(f"Repair round failed: {fc['repair_error']}")
    return "\n".join(out)


def card_text(row: sqlite3.Row) -> str:
    """The end card as `show` and `send` print it: its lines, what was left off, the fact-check."""
    from reelline.pipeline.endcard import summary_lines

    card = json.loads(row["passes"] or "{}").get("card")
    out = summary_lines(card)
    if card:
        out = [f"  {k}: {v}" for k, v in card["lines"].items()] + out
        for n, r in enumerate(card.get("rounds", []), 1):
            c = r["counts"]
            out.append(
                f"Card fact-check round {n}: {c['supported']} supported, {c['unsupported']} unsupported, "
                f"{c['distorted']} distorted"
            )
        if card.get("repair_error"):
            out.append(f"Card repair round failed: {card['repair_error']}")
    return "\n".join(out)


def fx_text(row: sqlite3.Row) -> str:
    """The fact graphics as `show` and `send` print them: shown, left off and why."""
    from reelline.pipeline.fx import summary_lines

    passes = json.loads(row["passes"] or "{}")
    out = summary_lines(passes.get("fx")) or ["🎞 Fact graphics: none checked (a script made before them)"]
    return "\n".join(
        out
        + [
            f"  - fixed in code: {note}"
            for key, value in passes.items()
            if key.endswith("/punchup")
            for note in value.get("fx_repaired") or []
        ]
    )


def openings_text(row: sqlite3.Row) -> str | None:
    """The final draft's openings as `show` prints them (the story pass's OPENINGS block:
    the one used and the runners-up, with their rubric scores); None for a script without."""
    passes = json.loads(row["passes"] or "{}")
    openings = (passes.get("story+punchup/story") or passes.get("first/story") or {}).get("openings")
    if not openings:
        return None
    if "problem" in openings:
        return f"  none: {openings['problem']}"
    return "\n".join(
        [f"  used ({_scored(openings['used'])}): {openings['used']['opening']}"]
        + [f"  runner-up ({_scored(o)}): {o['opening']}" for o in openings["runners_up"]]
    )


def status_mark(status: str) -> str:
    return "⚠️ " if status == "flagged" else ""
