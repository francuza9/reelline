"""The review buttons: approve, regenerate, reject."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from reelline.config import conf
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline.approve_bot.base import (
    JOB_LABELS,
    hm,
)
from reelline.pipeline.approve_bot.jobs import (
    active_job,
    add_job,
)
from reelline.pipeline.approve_bot.state import BotState
from reelline.pipeline.approve_bot.status import slot_picking
from reelline.pipeline.scoring import slot_datetime, tbilisi
from reelline.review import machine as review
from reelline.review.base import Tap


class ReviewTapsMixin(BotState):
    STATUS_ANSWER = {
        "approved": "already approved",
        "rejected": "already rejected",
        "superseded": "superseded by a newer reel",
        "expired": "expired",
    }

    def on_review_tap(self, query: Tap, review_id: int, action: str) -> None:
        r = review.get(self.conn, review_id)
        if r is None:
            self.answer(query, "expired")
            return
        if action == "unschedule":
            self.on_unschedule(query, r, query.user)
            return
        if r["status"] != "pending":
            self.answer(query, self.STATUS_ANSWER[r["status"]])
            return
        pick = self.pick(r["pick_id"])
        if r["state"] == "working" or active_job(self.conn, pick["slot_date"], pick["slot_time"]):
            self.answer(query, "already running")
            return
        if action not in review.STATE_ACTIONS[r["state"]]:
            self.answer(query, "expired")
            self.refresh(review_id)  # re-sync the buttons with the state
            return
        by = query.user
        if action == "approve":
            reel = self.conn.execute("SELECT qa_status FROM reels WHERE id = ?", (r["reel_id"],)).fetchone()
            if reel["qa_status"] == "fail":
                self.refresh(review_id, state="confirm_qa")
                self.answer(query, "QA failed: approve anyway?")
            else:
                self.approve_or_ask(query, r, by)
        elif action == "qa_yes":
            self.approve_or_ask(query, r, by)
        elif action in ("qa_no", "back"):
            self.refresh(review_id, state="main", reject_reason=None)
            self.answer(query, "OK")
        elif action == "post_now_ask":
            self.refresh(review_id, state="confirm_now")
            self.answer(query, "Post now?")
        elif action == "post_now":  # confirm_late's Post now, or confirm_now's Confirm
            self.approve(query, r, by, self.now(), "now")
        elif action == "next_slot":
            free = self.next_free_slot()
            if free is None:
                self.answer(query, "No free slot in the next 14 days")
                return
            if (free[0], free[1]) != (pick["slot_date"], pick["slot_time"]):
                db.update_pick(self.conn, pick["id"], slot_date=free[0], slot_time=free[1])
            self.approve(query, r, by, slot_datetime(date.fromisoformat(free[0]), free[1]), "slot")
        elif action == "caption":
            self.refresh(review_id, state="caption", caption_requested_at=db.now_iso(self.now()))
            self.say(
                about_reel(
                    r["reel_id"],
                    "✏️ Send the new caption: the hook, and optionally a last line of "
                    "only hashtags to replace them. /cancel aborts.",
                )
            )
            self.answer(query, "Send the new caption")
        elif action == "regenerate":
            if review.regen_left(self.conn, r) <= 0:
                self.answer(query, "regeneration limit reached")
                self.refresh(review_id)
                return
            self.refresh(review_id, state="regen")
            self.answer(query, "Regenerate how?")
        elif action in ("new_script", "new_voice", "new_images", "other_story"):
            self.regenerate(query, r, action, by)
        elif action == "reject":
            self.refresh(review_id, state="reject")
            self.answer(query, "Why?")
        elif action in review.REJECT_REASONS:
            self.refresh(review_id, state="reject_next", reject_reason=action)
            self.answer(query, "Other story or skip?")
        elif action in ("reject_other_story", "skip_slot"):
            self.reject(query, r, by, other_story=action == "reject_other_story")

    def approve_or_ask(self, query: Tap, r: sqlite3.Row, by: str) -> None:
        pick = self.pick(r["pick_id"])
        slot_dt = self.slot_dt(pick)
        if self.now() < slot_dt:
            self.approve(query, r, by, slot_dt, "slot")
        else:
            self.refresh(r["id"], state="confirm_late")
            self.answer(query, "The slot has passed")

    def approve(self, query: Tap, r: sqlite3.Row, by: str, publish_at: datetime, mode: str) -> None:
        from reelline.pipeline import publish

        now = self.now()
        pick = self.pick(r["pick_id"])
        reel = self.conn.execute("SELECT * FROM reels WHERE id = ?", (r["reel_id"],)).fetchone()
        with db.transaction(self.conn):
            self.conn.execute(
                """UPDATE reels SET status = 'approved', approved_at = ?, approved_by = ?, publish_at = ?,
                                     publish_mode = ?, updated_at = ? WHERE id = ?""",
                (db.now_iso(now), by, db.now_iso(publish_at), mode, db.now_iso(now), reel["id"]),
            )
            db.update_pick(self.conn, pick["id"], status="approved")
            self.conn.execute(
                "UPDATE scripts SET status = 'approved', updated_at = ? WHERE id = ?",
                (db.now_iso(now), reel["script_id"]),
            )
        anyway = " anyway (QA ❌)" if reel["qa_status"] == "fail" else ""
        where = (
            "posting now"
            if mode == "now"
            else f"{pick['slot_time']} slot"
            + (f" ({pick['slot_date']})" if pick["slot_date"] != now.astimezone(tbilisi()).date().isoformat() else "")
        )
        self.refresh(
            r["id"],
            status="approved",
            state="main",
            decided_at=db.now_iso(now),
            decided_by=by,
            line=f"✅ approved{anyway} {hm(now)} - {where}",
        )
        self.answer(query, "approved")
        self.log(f"Reel #{reel['id']} approved by {by}: {where}")
        publish.enqueue(
            self.conn, reel["id"], send=lambda text: self.say(text, reply_to=r["message_id"]), log=self.log, now=now
        )
        if conf().publish.enabled:
            self.publish_tick()

    def next_free_slot(self) -> tuple[str, str] | None:
        """The nearest slot after now with no live pick and nothing picking for it."""
        now = self.now()
        today = now.astimezone(tbilisi()).date()
        for d in range(15):
            day = today + timedelta(days=d)
            for slot in conf().channel.slots:
                if (
                    slot_datetime(day, slot) > now
                    and db.slot_pick(self.conn, day.isoformat(), slot) is None
                    and not slot_picking(self.conn, day.isoformat(), slot)
                ):
                    return day.isoformat(), slot
        return None

    def regenerate(self, query: Tap, r: sqlite3.Row, kind: str, by: str) -> None:
        if review.regen_left(self.conn, r) <= 0:
            self.answer(query, "regeneration limit reached")
            self.refresh(r["id"], state="main")
            return
        job_id = add_job(
            self.conn, kind, self.pick(r["pick_id"]), r["id"], story_fate="rejected" if kind == "other_story" else None
        )
        if job_id is None:
            self.answer(query, "already running")
            return
        self.refresh(
            r["id"], state="working", line=f"⏳ regenerating ({JOB_LABELS[kind]}) {hm(self.now())}, job #{job_id}"
        )
        self.answer(query, "⏳ regenerating...")
        self.log(f"Job #{job_id} ({kind}) for review #{r['id']} by {by}")
        self.dispatch()

    def reject(self, query: Tap, r: sqlite3.Row, by: str, other_story: bool) -> None:
        reason = r["reject_reason"]
        if other_story and review.regen_left(self.conn, r) <= 0:
            self.answer(query, "regeneration limit reached")
            self.refresh(r["id"])
            return
        pick = self.pick(r["pick_id"])
        reel = self.conn.execute("SELECT * FROM reels WHERE id = ?", (r["reel_id"],)).fetchone()
        fate = "rejected" if reason in conf().review.approve.reject_story_reasons else "eligible"
        now = self.now()
        with db.transaction(self.conn):
            if other_story:
                job_id = add_job(self.conn, "other_story", pick, r["id"], story_fate=fate)
                if job_id is None:
                    self.answer(query, "already running")
                    return
            self.conn.execute(
                """INSERT INTO rejections (reel_id, pick_id, script_id, story_id, reason, next, by,
                                     created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    reel["id"],
                    pick["id"],
                    reel["script_id"],
                    pick["story_id"],
                    reason,
                    "other_story" if other_story else "skip_slot",
                    by,
                    db.now_iso(now),
                ),
            )
            self.conn.execute(
                "UPDATE reels SET status = 'rejected', updated_at = ? WHERE id = ?", (db.now_iso(now), reel["id"])
            )
            if not other_story:
                db.update_pick(
                    self.conn,
                    pick["id"],
                    status="skipped",
                    story_status="rejected" if fate == "rejected" else "new",
                    story_reason=f"reviewer: {reason}" if fate == "rejected" else None,
                    note=f"skipped by the reviewer: {reason}",
                )
        label = conf().review.approve.labels[reason]
        then = "looking for another story" if other_story else f"{pick['slot_time']} slot skipped"
        self.refresh(
            r["id"],
            status="rejected",
            state="main",
            decided_at=db.now_iso(now),
            decided_by=by,
            line=f"❌ rejected ({label}) {hm(now)} - {then}",
        )
        self.answer(query, "rejected")
        self.log(f"Reel #{reel['id']} rejected by {by} ({reason}); {then}")
        if other_story:
            self.dispatch()
