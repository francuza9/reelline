"""The review sent to the review channel."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from reelline.notify.base import NotifyError, split_text
from reelline.notify.text import about_reel
from reelline.paths import stored_path
from reelline.pipeline import db
from reelline.pipeline import produce as _p
from reelline.pipeline import qa as qa_mod
from reelline.pipeline.produce.base import ProduceError, rel_path
from reelline.pipeline.produce.report import status_block
from reelline.pipeline.produce.store import update_reel


class Review:
    """The real review channel (review.channel, reelline/review/; Telegram: the reel and cover
    through scripts/send_to_phone.py, text and the buttons in the review chat)."""

    def __init__(self, channel=None):
        from reelline.review import get_review_channel

        self.channel = get_review_channel(channel)

    def send_reel(self, mp4: Path, header: str) -> None:
        try:
            self.channel.send_video(mp4, header)
        except NotifyError as exc:
            raise ProduceError(f"review send: {exc}") from None

    def send_text(self, text: str) -> None:
        for chunk in split_text(text, self.channel.text_limit):
            self.channel.send(chunk)

    def send_review(self, conn: sqlite3.Connection, reel_id: int, text: str) -> int:
        """The status block with the review buttons (reelline/review/machine.py); the review id."""
        from reelline.review import machine as review

        return review.send(conn, self.channel, reel_id, text)


def review_part(
    conn: sqlite3.Connection,
    reel_id: int,
    slot_date: str,
    slot_time: str,
    review_factory: Callable[[], object] = Review,
    clock: Callable[[], datetime] | None = None,
    log: Callable[[str], None] = print,
) -> int | None:
    """The reel's review sent, unless it's held for the evening delivery or the delivery sent it:
    the review id, or None."""
    from reelline.pipeline import run_daily
    from reelline.pipeline.scoring import tbilisi

    at = (clock or run_daily.utcnow)()
    if run_daily.review_held(conn, slot_date, slot_time, at):
        log(f"Review held for the evening delivery ({run_daily.delivery_at(slot_date).astimezone(tbilisi()):%H:%M})")
    elif not run_daily.claim_review(conn, reel_id, at):
        log("Review already sent (the evening delivery)")
    else:
        review_id = send_review_messages(conn, reel_id, review_factory())
        log(f"Review #{review_id} sent to Telegram")
        return review_id
    return None


def review_rendered(
    conn: sqlite3.Connection,
    reel_id: int,
    source_names: dict[str, str],
    *,
    checker_factory: Callable[[], object] | None = None,
    review_factory: Callable[[], object] = Review,
    clock: Callable[[], datetime] | None = None,
    log: Callable[[str], None] = print,
) -> str:
    """A rendered reel whose worker stopped before its review (the tick's qa_review job,
    reelline/pipeline/run_daily.py): QA on the render as it is (no re-roll, no re-render), stored
    on the reel row and in qa_<reel id>.json, then the review, unless it's held for the
    evening delivery or one was sent."""
    from reelline.pipeline import run_daily
    from reelline.review import machine as review_mod

    reel = conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()
    if reel is None or reel["status"] != "rendered":
        raise ProduceError(f"Reel #{reel_id} is {reel['status'] if reel else 'missing'}; only a rendered reel")
    pick = conn.execute("SELECT * FROM picks WHERE id = ?", (reel["pick_id"],)).fetchone()
    mp4 = stored_path(reel["mp4_path"])
    subject = qa_mod.subject_from_dir(
        mp4.parent,
        **qa_mod.caption_fields(conn, reel["script_id"], source_names, caption=reel["caption"], reel_id=reel_id),
    )
    subject.check_these = json.loads(reel["check_these"] or "[]")
    subject.cover_fresh = True if reel["cover_path"] else None
    try:
        report = (checker_factory or qa_mod.Checker)().run(subject)
    except qa_mod.QaError as exc:
        report = qa_mod.Report([qa_mod.Check("qa", "error", "fail", str(exc), None, [f"QA could not run: {exc}"])])
    db.record_calls(conn, report.calls, script_id=reel["script_id"], reel_id=reel_id)
    outcome = qa_mod.Outcome(None, report, {"rerolls": [], "renders": [], "reports": [report]})
    path = qa_mod.write_qa_json(mp4.parent, qa_mod.qa_json(outcome), reel_id)
    update_reel(
        conn,
        reel_id,
        qa_status=report.status,
        qa_failures=json.dumps(report.failures, ensure_ascii=False),
        qa_warnings=json.dumps(report.warnings, ensure_ascii=False),
        qa_path=rel_path(path),
    )
    qa = "QA ✅" if report.status == "pass" else f"QA ❌ ({'; '.join(report.failures)})"
    log(f"{qa} for reel #{reel_id} (the render as it is)")
    at = (clock or run_daily.utcnow)()
    if run_daily.review_held(conn, pick["slot_date"], pick["slot_time"], at):
        return f"reel #{reel_id}: {qa}, review held for the evening delivery"
    if review_mod.for_reel(conn, reel_id) is not None:
        return f"reel #{reel_id}: {qa}, review already sent"
    run_daily.claim_review(conn, reel_id, at)  # the marker; a claim whose send never happened doesn't stop it
    review_id = send_review_messages(conn, reel_id, review_factory())
    log(f"Review #{review_id} sent to Telegram")
    return f"reel #{reel_id}: {qa}, review #{review_id}"


def send_review_messages(conn: sqlite3.Connection, reel_id: int, review) -> int:
    """A rendered reel's review: the header, the video, the caption, then the status block
    with the buttons. Returns the review id."""
    from reelline.review.machine import header_text

    reel = conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()
    review.send_text(header_text(conn, reel_id))
    review.send_reel(stored_path(reel["mp4_path"]), about_reel(reel_id, reel["name"]))
    review.send_text(reel["caption"])
    return review.send_review(conn, reel_id, status_block(conn, reel))


def deliver_review(conn: sqlite3.Connection, reel_id: int) -> int:
    """A held review sent (the evening delivery, reelline/pipeline/run_daily.py deliver())."""
    return send_review_messages(conn, reel_id, _p.Review())
