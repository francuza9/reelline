"""The produce run: render, QA, review."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime

from reelline.config import conf
from reelline.notify.text import about_reel
from reelline.paths import stored_path
from reelline.pipeline import db
from reelline.pipeline.produce.backgrounds import BackgroundChoice, choose_background, choose_mood
from reelline.pipeline.produce.base import Produced, ProduceError, RenderFailed, attempts_error, rel_path, short_error
from reelline.pipeline.produce.qa import qa_part, skip_qa
from reelline.pipeline.produce.rendering import RENDER_OPTIONS, Rendered, Renderer, RenderJob, render_reel
from reelline.pipeline.produce.review import Review, review_part
from reelline.pipeline.produce.store import insert_reel, latest_script, slot_pick, supersede, update_reel


def produce(conn: sqlite3.Connection, slot_date: str, slot_time: str, source_names: dict[str, str], **kw) -> Produced:
    """_produce() with every paid call's cost row written the moment the call returns
    (reelline/providers/costs.py), so a worker killed mid-render loses no spend; the reel is added
    to those rows once it exists."""
    from reelline.providers.costs import recording

    script = latest_script(conn, slot_pick(conn, slot_date, slot_time)["id"])
    with recording(db.live_recorder(conn, script_id=script["id"])):
        return _produce(conn, slot_date, slot_time, source_names, **kw)


def _produce(
    conn: sqlite3.Connection,
    slot_date: str,
    slot_time: str,
    source_names: dict[str, str],
    *,
    background: str | None = None,
    send: bool = True,
    renderer: Renderer = render_reel,
    review_factory: Callable[[], object] = Review,
    log: Callable[[str], None] = print,
    qa: bool = True,
    checker_factory: Callable[[], object] | None = None,
    keep_background: str | None = None,
    alert: bool = True,
    render_options: dict | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Produced:
    """keep_background: a regeneration's background (recorded as "kept"); alert False:
    no Telegram alert for a failed render (the bot's job reports it); render_options:
    RENDER_OPTIONS for the first render. The review is sent unless the slot's evening
    chain holds it for the evening delivery (reelline/pipeline/run_daily.py review_held(); the
    tick sends it then), or the delivery already sent it (claim_review()). The three parts are
    the render, qa and review steps (reelline/steps/)."""
    job = render_part(
        conn,
        slot_date,
        slot_time,
        source_names,
        background=background,
        renderer=renderer,
        log=log,
        keep_background=keep_background,
        render_options=render_options,
    )
    if isinstance(job, tuple):
        return failed_alert(*job, send and alert, review_factory, log)
    if qa:
        failed = qa_part(conn, job, source_names, checker_factory, log)
        if failed is not None:
            return failed_alert(failed, failed.error, send and alert, review_factory, log)
    else:
        skip_qa(conn, job.reel_id, log)
    if send:
        review_part(conn, job.reel_id, slot_date, slot_time, review_factory, clock, log)
    return Produced(job.reel_id, "rendered")


def failed_alert(result: Produced, text: str, alert: bool, review_factory: Callable[[], object], log) -> Produced:
    """A failed render's alert, when alert."""
    if alert:
        review_factory().send_text(text)
        log("Alert sent to Telegram")
    else:
        log("Alert not sent:\n" + text)
    return result


def render_part(
    conn: sqlite3.Connection,
    slot_date: str,
    slot_time: str,
    source_names: dict[str, str],
    *,
    background: str | None = None,
    renderer: Renderer = render_reel,
    log: Callable[[str], None] = print,
    keep_background: str | None = None,
    render_options: dict | None = None,
) -> RenderJob | tuple[Produced, str]:
    """The slot's script rendered (render_attempts tries), its reel row and caption: a RenderJob,
    or (the render_failed Produced, the alert's text)."""
    from reelline.pipeline.caption import CaptionError, assemble_caption, attributions_path, images_json_path

    pick = slot_pick(conn, slot_date, slot_time)
    script = latest_script(conn, pick["id"])
    script_path = stored_path(script["path"])
    choice = BackgroundChoice(keep_background, "kept") if keep_background else choose_background(conn, background)
    options = render_options or {}
    unknown = set(options) - set(RENDER_OPTIONS)
    if unknown:
        raise ProduceError(f"Unknown render option(s): {', '.join(sorted(unknown))}")
    mood = choose_mood(conn, pick, script)
    log(
        f"Pick #{pick['id']} ({slot_date} {slot_time}), script #{script['id']} ({script['status']}): {script_path.name}"
    )
    log(f"Background: {choice.name} ({choice.source}), mood {mood}")

    started = time.monotonic()
    errors: list[str] = []
    failed_calls: list[dict] = []  # what the failed attempts spent: recorded with the reel
    rendered: Rendered | None = None
    for attempt in range(1, conf().steps.produce.render_attempts + 1):
        try:
            rendered = renderer(script_path, choice.name, mood, [], **options)
            break
        except RenderFailed as exc:
            errors.append(str(exc))
            failed_calls += exc.paid_calls
            log(f"Render attempt {attempt}/{conf().steps.produce.render_attempts} failed: {exc}")
        except BaseException as exc:  # not a render failure: record the spend, then crash
            db.record_calls(conn, failed_calls + getattr(exc, "paid_calls", []), script_id=script["id"])
            raise
    render_s = round(time.monotonic() - started, 1)
    attempts = len(errors) + (1 if rendered else 0)
    now = db.now_iso()
    base = dict(
        pick_id=pick["id"],
        script_id=script["id"],
        name=script_path.stem,
        background=choice.name,
        background_source=choice.source,
        mood=mood,
        attempts=attempts,
        render_s=render_s,
        opening_version=conf().render.opening_version,
        created_at=now,
        updated_at=now,
    )

    if rendered is None:
        error = attempts_error(errors)
        reel_id = insert_reel(conn, status="render_failed", error=error, **base)
        db.record_calls(conn, failed_calls, script_id=script["id"], reel_id=reel_id)
        db.update_pick(conn, pick["id"], status="render_failed")
        text = about_reel(
            reel_id,
            f"⚠️ Render failed: pick #{pick['id']} ({slot_date} {slot_time}), "
            f"script #{script['id']}, background {choice.name}\n\n{short_error(errors[-1])}",
        )
        return Produced(reel_id, "render_failed", error), text

    expected = attributions_path(script["path"])
    if rendered.attributions is not None and rendered.attributions.resolve() != expected.resolve():
        raise ProduceError(f"The render wrote {rendered.attributions}, but the caption reads {expected}")
    expected = images_json_path(script["path"])
    if rendered.images_json is not None and rendered.images_json.resolve() != expected.resolve():
        raise ProduceError(f"The render wrote {rendered.images_json}, but the caption reads {expected}")
    reel_id = insert_reel(
        conn,
        status="rendered",
        mp4_path=rel_path(rendered.mp4),
        cover_path=rel_path(rendered.cover),
        images_path=rel_path(rendered.images_json),
        attributions_path=rel_path(rendered.attributions),
        duration_s=round(rendered.duration, 2),
        check_these=json.dumps(rendered.check_these, ensure_ascii=False),
        tts_cost_usd=rendered.tts_cost_usd,
        **rendered.gag_fields(),
        **base,
    )
    db.update_pick(conn, pick["id"], status="rendered")
    supersede(conn, pick["id"], reel_id)
    db.record_calls(conn, failed_calls + rendered.paid_calls, script_id=script["id"], reel_id=reel_id)
    try:
        caption = assemble_caption(conn, script["id"], source_names, reel_id)
    except CaptionError as exc:
        update_reel(conn, reel_id, error=f"caption: {exc}")
        raise ProduceError(f"Reel #{reel_id} rendered, but its caption failed: {exc}") from None
    update_reel(conn, reel_id, caption=caption)
    log(f"Reel #{reel_id} rendered: {rendered.mp4} ({rendered.duration:.1f}s, {attempts} attempt(s), {render_s:.0f}s)")
    return RenderJob(reel_id, pick, script, script_path, choice.name, mood, rendered, renderer)
