"""QA on a fresh render, and the no-QA notice."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline import qa as qa_mod
from reelline.pipeline.produce.base import Produced, RenderFailed, rel_path, short_error
from reelline.pipeline.produce.rendering import Rendered, Renderer, RenderJob
from reelline.pipeline.produce.store import update_reel


def qa_part(
    conn: sqlite3.Connection,
    job: RenderJob,
    source_names: dict[str, str],
    checker_factory: Callable[[], object] | None,
    log: Callable[[str], None],
) -> Produced | None:
    """run_qa() on a fresh render: None, or a render_failed Produced (a render during QA failed)."""
    result = run_qa(
        conn,
        job.reel_id,
        job.pick,
        job.script,
        job.script_path,
        job.background,
        job.mood,
        job.rendered,
        source_names,
        job.renderer,
        checker_factory,
        log,
    )
    return None if result is None else Produced(job.reel_id, "render_failed", result)


def skip_qa(conn: sqlite3.Connection, reel_id: int, log: Callable[[str], None]) -> None:
    log(NO_QA_NOTICE)
    update_reel(conn, reel_id, qa_status="skipped")


NO_QA_NOTICE = (
    "\n" + "!" * 72 + "\n!!  QA SKIPPED (--no-qa): no automatic checks on this reel; the review says so.\n" + "!" * 72
)


def run_qa(
    conn: sqlite3.Connection,
    reel_id: int,
    pick: sqlite3.Row,
    script: sqlite3.Row,
    script_path: Path,
    background: str,
    mood: str,
    rendered: Rendered,
    source_names: dict[str, str],
    renderer: Renderer,
    checker_factory: Callable[[], object] | None,
    log: Callable[[str], None],
) -> str | None:
    """reelline/pipeline/qa.py run_flow() for the reel, its result stored on the reel row (and
    qa.json). Returns None, or the alert text when a render during QA failed (the reel
    and its pick are then render_failed)."""
    from reelline.media.script_markup import parse

    tag = len(parse(script_path.read_text(encoding="utf-8")).paragraphs)

    def subject_for(r: Rendered):
        fields = qa_mod.caption_fields(conn, script["id"], source_names, reel_id=reel_id)
        subject = qa_mod.subject_from_dir(r.mp4.parent, **fields)
        subject.check_these = r.check_these
        subject.cover_fresh = True if r.cover is not None else None
        return subject

    try:
        outcome = qa_mod.run_flow(
            rendered,
            lambda regen: renderer(script_path, background, mood, regen),
            subject_for,
            (checker_factory or qa_mod.Checker)(),
            tag,
            log,
        )
    except RenderFailed as exc:
        spent = getattr(exc, "qa_outcome", None)
        db.record_calls(
            conn, (spent.calls if spent is not None else []) + exc.paid_calls, script_id=script["id"], reel_id=reel_id
        )
        error = f"QA render: {exc}"
        update_reel(
            conn,
            reel_id,
            status="render_failed",
            error=error,
            qa_status="fail",
            qa_failures=json.dumps([error], ensure_ascii=False),
        )
        db.update_pick(conn, pick["id"], status="render_failed")
        return about_reel(
            reel_id,
            f"⚠️ Render failed during QA: pick #{pick['id']} ({pick['slot_date']} "
            f"{pick['slot_time']}), script #{script['id']}, background {background}\n\n"
            f"{short_error(str(exc))}",
        )
    except BaseException as exc:  # QA broke some other way: record what it spent, then crash
        spent = getattr(exc, "qa_outcome", None)
        db.record_calls(
            conn,
            (spent.calls if spent is not None else []) + getattr(exc, "paid_calls", []),
            script_id=script["id"],
            reel_id=reel_id,
        )
        raise
    final = outcome.rendered
    db.record_calls(conn, outcome.calls, script_id=script["id"], reel_id=reel_id)
    report = outcome.report
    path = qa_mod.write_qa_json(final.mp4.parent, qa_mod.qa_json(outcome), reel_id)
    tts = None if rendered.tts_cost_usd is None else rendered.tts_cost_usd + outcome.tts_new_cost_usd
    update_reel(
        conn,
        reel_id,
        mp4_path=rel_path(final.mp4),
        cover_path=rel_path(final.cover),
        images_path=rel_path(final.images_json),
        attributions_path=rel_path(final.attributions),
        duration_s=round(final.duration, 2),
        check_these=json.dumps(final.check_these, ensure_ascii=False),
        tts_cost_usd=tts,
        caption=subject_for(final).caption,
        qa_status=report.status,
        qa_failures=json.dumps(report.failures, ensure_ascii=False),
        qa_warnings=json.dumps(report.warnings, ensure_ascii=False),
        qa_path=rel_path(path),
        **final.gag_fields(),
    )
    log(
        f"QA {'✅' if report.status == 'pass' else '❌'} for reel #{reel_id}: {len(report.failures)} failure(s), "
        f"{len(report.warnings)} warning(s), {len(outcome.renders)} extra render(s); {path}"
    )
    return None
