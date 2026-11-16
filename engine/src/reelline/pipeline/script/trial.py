"""The trial command: the chain's passes for a pick without storing a script."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime

from reelline.config import conf
from reelline.media import style as reel_style
from reelline.media.script_markup import (
    parse,
)
from reelline.paths import REPO_ROOT
from reelline.pipeline import db
from reelline.pipeline import script as _s
from reelline.pipeline.script.base import NO_RECENT_LINES, ScriptError
from reelline.pipeline.script.lock import details_line, flirt_line, share_line
from reelline.pipeline.script.openings import openings_note
from reelline.pipeline.script.punchup import punchup_pass
from reelline.pipeline.script.recent import banned_phrase_ok, recent_details, recent_flirt_lines, recent_shares
from reelline.pipeline.script.story import hook_examples, story_pass


def trial(
    conn: sqlite3.Connection,
    claude,
    brief_id: int,
    log=lambda m: print(m, flush=True),
    flirt: bool = False,
    angle: str | None = None,
) -> dict:
    """story -> punch-up (with a flirt line when `flirt`, on `angle`, else one of
    FLIRT_ANGLES at random) -> proofread from brief #brief_id, and the proofread of its last
    hook; nothing stored but the cost rows (manual). The trial's report. In a style without
    jokes (reelline/media/style.py straight) there's no flirt line, and no recent lines or previous
    opening are fed."""
    import random

    from reelline.pipeline.proofread import proofread_hook, proofread_script

    style = reel_style.current()
    if flirt and not style.jokes:
        log(f"no flirt line in the {style.name} style (reelline/media/style.py): --flirt on ignored")
        flirt = False
    angle = (angle or random.choice(conf().steps.script.flirt_angles)) if flirt else None

    brief = conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    if brief is None:
        raise ScriptError(f"No brief #{brief_id}")
    pick = (
        conn.execute("SELECT slot_date FROM picks WHERE id = ?", (brief["pick_id"],)).fetchone()
        if brief["pick_id"]
        else None
    )
    publish_date = date.fromisoformat(pick["slot_date"]) if pick and pick["slot_date"] else None
    examples, examples_note = hook_examples()
    log(examples_note)
    with db.cost_context(manual=True):
        draft = story_pass(claude, brief["markdown"], publish_date=publish_date, conn=conn, examples=examples)
        punch = punchup_pass(
            claude,
            brief["markdown"],
            draft.text,
            _s.recent_lines(conn) if style.jokes else NO_RECENT_LINES,
            previous=_s.previous_opening(conn) if style.jokes else None,
            publish_date=publish_date,
            conn=conn,
            flirt=flirt,
            recent_flirt_lines=recent_flirt_lines(conn),
            flirt_angle=angle,
            banned_ok=banned_phrase_ok(conn),
            recent_details_lines=recent_details(conn),
            recent_share_lines=recent_shares(conn),
            log=log,
        )
        pr = proofread_script(claude, punch.text, conn=conn, log=log)
        cap = conn.execute(
            """SELECT c.* FROM captions c JOIN scripts s ON s.id = c.script_id WHERE s.brief_id = ?
                              ORDER BY c.id DESC LIMIT 1""",
            (brief_id,),
        ).fetchone()
        hook = (
            proofread_hook(claude, cap["hook"], cap["youtube_title"], json.loads(cap["hashtags"]), conn=conn, log=log)
            if cap
            else None
        )
    by_pass = {
        "story": sum(r.cost_usd or 0 for r in draft.replies),
        "punchup": sum(r.cost_usd or 0 for r in punch.replies),
        "proofread": sum(r.cost_usd or 0 for r in pr.replies),
        "proofread_hook": sum(r.cost_usd or 0 for r in hook.replies) if hook else 0.0,
    }
    return {
        "brief_id": brief_id,
        "draft": draft.text,
        "openings": draft.openings,
        "before": punch.text,
        "after": pr.text,
        "applied": pr.applied,
        "flirt": flirt_line(parse(pr.text)) if flirt else None,
        "angle": angle,
        "details": details_line(parse(pr.text)),
        "share": share_line(parse(pr.text)),
        "punchup_retried": punch.retried,
        "reason": pr.reason,
        "fixes": pr.fixes,
        "hook": None
        if hook is None
        else {
            "before": hook.original,
            "after": hook.text,
            "applied": hook.applied,
            "reason": hook.reason,
            "fixes": hook.fixes,
        },
        "cost_usd": {k: round(v, 4) for k, v in by_pass.items()},
        "total_usd": round(sum(by_pass.values()), 4),
    }


def trial_text(report: dict) -> str:
    out = (
        [f"== Brief #{report['brief_id']}"]
        + ([f"💘 flirt line ({report.get('angle')}): {report['flirt']}"] if report.get("flirt") else [])
        + ([f"🪧 details line: {report['details']}"] if report.get("details") else [])
        + ([f"📤 share line: {report['share']}"] if report.get("share") else [])
        + ([f"punch-up retried: {report['punchup_retried']}"] if report.get("punchup_retried") else [])
        + (
            [f"openings: {openings_note(report['openings'])}"]
            + [f"  runner-up: {o['opening']}" for o in (report["openings"] or {}).get("runners_up", [])]
            if "openings" in report
            else []
        )
        + [
            "",
            "-- Before the proofread",
            report["before"],
            "",
            "-- After" + ("" if report["applied"] else f" (kept the original: {report['reason']})"),
            report["after"],
            "",
            f"-- Fixes ({len(report['fixes'])})",
        ]
    )
    out += [f"  {f['before']}  →  {f['after']}" for f in report["fixes"]] or ["  none"]
    if report["hook"]:
        h = report["hook"]
        out += [
            "",
            "-- Hook",
            f"  before: {h['before']}",
            f"  after:  {h['after']}" + ("" if h["applied"] else f" (kept: {h['reason']})"),
        ]
        out += [f"  {f['before']}  →  {f['after']}" for f in h["fixes"]]
    out += [
        "",
        "-- Cost: "
        + ", ".join(f"{k} ${v:.4f}" for k, v in report["cost_usd"].items())
        + f"; total ${report['total_usd']:.4f}",
    ]
    return "\n".join(out)


def cmd_trial(args, conn, names) -> int:
    from reelline.providers import get_llm

    claude = get_llm("writer")
    out_dir = REPO_ROOT / "out" / "trials"
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for brief_id in args.brief:
        report = trial(conn, claude, brief_id, flirt=args.flirt == "on")
        reports.append(report)
        print(trial_text(report) + "\n", flush=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"trial_{stamp}.json"
    path.write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Report: {path}")
    return 0
