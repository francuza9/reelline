"""The run and show commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from reelline.pipeline import db
from reelline.pipeline.script.base import ScriptError
from reelline.pipeline.script.chain import write_script
from reelline.pipeline.script.files import (
    card_text,
    complete,
    cost_lines,
    factcheck_summary,
    fx_text,
    openings_text,
    status_mark,
)
from reelline.providers import TransportFailure


def cmd_run(args, conn, names) -> int:
    from reelline.pipeline.caption import assemble_caption
    from reelline.pipeline.scoring import tbilisi
    from reelline.providers import get_llm

    if args.pick is not None:
        pick_id = args.pick
    else:
        if not args.slot:
            raise ScriptError("give --pick ID or --slot HH:MM")
        day = args.date or datetime.now(UTC).astimezone(tbilisi()).date().isoformat()
        row = db.slot_pick(conn, day, args.slot)
        if row is None:
            raise ScriptError(f"No pick for {day} {args.slot}")
        pick_id = row["id"]
    claude, gemini = get_llm("writer"), get_llm("checker")
    try:
        run = write_script(conn, pick_id, claude=claude, gemini=gemini, source_names=names)
    except (ScriptError, TransportFailure) as exc:
        print(f"FAILED: {exc}")
        return 1
    path = complete(conn, run, claude)
    print(f"{status_mark(run.status)}Script #{run.script_id} {run.status}: {path}")
    print("\n" + assemble_caption(conn, run.script_id, names))
    print("\nCalls:\n" + "\n".join(cost_lines(conn, run.script_id)))
    return 0


def cmd_show(args, conn, names) -> int:
    from reelline.pipeline.caption import CaptionError, assemble_caption

    row = conn.execute("SELECT * FROM scripts WHERE id = ?", (args.script_id,)).fetchone()
    if row is None:
        raise ScriptError(f"No script #{args.script_id}")
    print(
        f"{status_mark(row['status'])}Script #{row['id']}: {row['status']} (pick #{row['pick_id']}, story "
        f"#{row['story_id']}, attempt {row['attempt']}) {row['path'] or ''}"
    )
    if row["error"]:
        print(f"Error: {row['error']}")
    print(f"\n== Draft\n{row['draft'] or '-'}")
    openings = openings_text(row)
    if openings:
        print(f"\n== Openings\n{openings}")
    print(f"\n== Final\n{row['final'] or '-'}")
    if row["draft_lock"]:
        report = json.loads(row["draft_lock"])
        print(f"\n== Draft lock: {report['counts']}")
        for c in report["changed"]:
            print(f"  changed ({c['ratio']}): {c['draft']}\n        -> {c['final']}")
        for d in report["dropped"]:
            print(f"  DROPPED: {d}")
        for i in report["inserted"]:
            print(f"  inserted: {i}")
    from reelline.pipeline.proofread import status_lines

    grammar = status_lines(conn, row["id"], max_chars=200)
    if grammar:
        print("\n== Proofread\n" + "\n".join(grammar))
    if row["factcheck"]:
        print(f"\n== Fact-check\n{factcheck_summary(row)}")
    print("\n== End card\n" + card_text(row))
    print("\n== Fact graphics\n" + fx_text(row))
    try:
        print(f"\n== Caption\n{assemble_caption(conn, row['id'], names)}")
    except CaptionError as exc:
        print(f"\n== Caption\n- ({exc})")
    print("\n== Calls\n" + "\n".join(cost_lines(conn, row["id"])))
    return 0
