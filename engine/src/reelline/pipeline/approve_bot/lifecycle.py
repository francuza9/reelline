"""The bot's jobs, periodic work, /status and the polling loop."""

from __future__ import annotations

import sqlite3
import time
import traceback
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.notify.base import NotifyError
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline.approve_bot.base import (
    WORKER_DIED,
    hm,
)
from reelline.pipeline.approve_bot.jobs import (
    active_job,
    add_job,
    dispatch_queued,
    get_job,
    reap_overdue,
    update_job,
)
from reelline.pipeline.approve_bot.reporting import (
    failure_text,
    notify_reaped,
    pick_line,
    report_failure,
)
from reelline.pipeline.approve_bot.state import BotState
from reelline.pipeline.approve_bot.status import expires_at, slot_state, status_text
from reelline.review import machine as review
from reelline.review.base import Tap


class LifecycleMixin(BotState):
    def on_picked(self, pick_id: int) -> None:
        """A menu answered (or timed out): the script and the reel, in a worker job."""
        pick = self.pick(pick_id)
        if pick["chosen_by"] == "timeout":
            story = db.get_story(self.conn, pick["story_id"])
            self.say(
                f"⏱ No answer to the {pick['slot_time']} menu ({pick['slot_date']}): took #1 automatically "
                f"({story['title'][:80]})."
            )
        job_id = add_job(self.conn, "script_produce", pick, exclusive=False)
        self.say(pick_line(self.conn, pick, f"writing the script and the reel (job #{job_id})."))
        self.dispatch()

    def on_menu_none(self, menu: sqlite3.Row) -> None:
        """None of these (inside the transaction that closes the menu): a pick job for the
        menu's slot, without a menu; handle() dispatches it once committed."""
        job_id = add_job(
            self.conn,
            "pick",
            {"slot_date": menu["slot_date"], "slot_time": menu["slot_time"], "id": None},
            exclusive=False,
            detail={"menu": False, "after_menu": menu["id"]},
        )
        self.log(f"Menu {menu['id']}: None of these; pick job #{job_id} for {menu['slot_date']} {menu['slot_time']}")
        self.none_job = job_id

    def on_retry(self, query: Tap, job_id: int) -> None:
        job = get_job(self.conn, job_id)
        if job is None:
            self.answer(query, "expired")
            return
        if job["status"] != "failed":
            self.answer(query, f"already {job['status']}")
            return
        other = active_job(self.conn, job["slot_date"], job["slot_time"])
        if other is not None:
            self.answer(query, "already running")
            return
        update_job(self.conn, job_id, status="queued", error=None, pid=None, started_at=None, finished_at=None)
        if job["message_id"]:
            try:
                self.tg.edit(job["message_id"], f"{failure_text(self.conn, job)}\n\n🔁 retrying {hm(self.now())}")
            except NotifyError as exc:
                self.log(f"job #{job_id}: edit failed: {exc}")
        r = review.get(self.conn, job["review_id"]) if job["review_id"] else None
        if r is not None and r["status"] == "pending":
            self.refresh(r["id"], state="working", line=f"🔁 retrying job #{job_id} {hm(self.now())}")
        self.answer(query, "⏳ retrying...")
        self.dispatch()

    def dispatch(self) -> None:
        """Start queued jobs whose slot has nothing running; reap finished children."""
        for job_id, child in list(self.children.items()):
            if child.poll() is not None:
                del self.children[job_id]
        dispatch_queued(self.conn, self._spawn, self.alive, self.log, self.fail_job, self.children)

    def fail_job(self, job: sqlite3.Row, error: str) -> None:
        update_job(self.conn, job["id"], status="failed", error=error, finished_at=db.now_iso(self.now()))
        report_failure(self.conn, self.tg, get_job(self.conn, job["id"]), log=self.log)

    def check_jobs(self) -> None:
        reap_overdue(
            self.conn,
            self.now(),
            self.alive,
            self.kill,
            lambda job, text: notify_reaped(self.conn, self.tg, self.say, job, text, log=self.log),
            self.log,
        )
        for job in self.conn.execute("SELECT * FROM jobs WHERE status = 'running'").fetchall():
            if not self.alive(job["pid"], job["id"]):
                self.log(f"Job #{job['id']} died (pid {job['pid']}, step {job['step']})")
                self.fail_job(job, WORKER_DIED)
        self.dispatch()

    # -- periodic --

    def tick(self) -> None:
        now = self.now()
        self.check_jobs()
        try:
            self.publish_tick()
        except Exception:  # an API being down mustn't stop the bot
            self.log(f"publish tick failed:\n{traceback.format_exc()}")
        try:
            self.picker.expire()
        except Exception as exc:  # a menu that can't resolve shouldn't stop the bot
            self.log(f"menu expiry failed: {exc}")
        cutoff = now - timedelta(minutes=conf().review.approve.caption_edit_timeout_min)
        for r in self.conn.execute("SELECT * FROM reviews WHERE status = 'pending' AND state = 'caption'").fetchall():
            if datetime.fromisoformat(r["caption_requested_at"]) <= cutoff:
                self.refresh(r["id"], state="main", line=f"✏️ caption edit timed out {hm(now)}")
        for r in self.conn.execute("SELECT * FROM reviews WHERE status = 'pending' AND state != 'working'").fetchall():
            sent = datetime.fromisoformat(r["sent_at"])
            if now >= expires_at(sent, self.slot_dt(self.pick(r["pick_id"]))):
                with db.transaction(self.conn):
                    self.conn.execute(
                        "UPDATE reels SET status = 'expired', updated_at = ? WHERE id = ?",
                        (db.now_iso(now), r["reel_id"]),
                    )
                self.refresh(
                    r["id"],
                    status="expired",
                    line=f"⌛ expired {hm(now)}: no action since it was sent ({hm(sent)}); not posted",
                )
                self.log(f"Review #{r['id']} (reel #{r['reel_id']}) expired")
                continue
            remind_at = self.slot_dt(self.pick(r["pick_id"])) - timedelta(
                minutes=conf().review.approve.reminder_before_slot_min
            )
            if r["reminded_at"] is None and sent <= remind_at <= now < self.slot_dt(self.pick(r["pick_id"])):
                pick = self.pick(r["pick_id"])
                left = int((self.slot_dt(pick) - now).total_seconds() // 60)
                self.say(
                    about_reel(
                        r["reel_id"], f"⏰ Still waiting for review: the {pick['slot_time']} slot is in {left} min."
                    ),
                    reply_to=r["message_id"],
                )
                review.update(self.conn, r["id"], reminded_at=db.now_iso(now))

    def recover(self) -> None:
        pending = self.conn.execute("SELECT COUNT(*) FROM reviews WHERE status = 'pending'").fetchone()[0]
        menus = len(db.open_menus(self.conn))
        jobs = self.conn.execute("SELECT * FROM jobs WHERE status IN ('queued', 'running')").fetchall()
        self.log(
            f"Re-attached: {pending} pending review(s), {menus} open menu(s), {len(jobs)} active job(s)"
            + "".join(
                f"\n  job #{j['id']} {j['kind']} {j['status']} (pid {j['pid']}, step {j['step']}, "
                f"{'alive' if self.alive(j['pid'], j['id']) else 'not running'})"
                for j in jobs
            )
        )
        self.tick()

    # -- /status --

    def status_text(self) -> str:
        return status_text(self.conn, self.now())

    def slot_state(self, slot_date: str, slot_time: str) -> str:
        return slot_state(self.conn, slot_date, slot_time)

    # -- the loop --

    def offset(self) -> int | str | None:
        """The review channel's cursor (Telegram: the next update_id), kept in bot_state 'offset'."""
        row = self.conn.execute("SELECT value FROM bot_state WHERE key = 'offset'").fetchone()
        if row is None:
            return None
        return int(row["value"]) if row["value"].isdigit() else row["value"]

    def save_offset(self, offset: int | str) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO bot_state (key, value) VALUES ('offset', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(offset),),
            )

    def poll_once(self, timeout: int) -> None:
        """One receive round of the review channel: each update's events handled, the cursor saved."""
        offset = self.offset()
        for update in self.tg.receive(None if offset is None else str(offset), timeout):
            if update.ignored is None:
                try:
                    for event in update.events:
                        self.on_event(event)
                except Exception as exc:  # never let one update stop the bot
                    self.log(f"update {update.id} failed:\n{traceback.format_exc()}")
                    self.say(f"⚠️ Bot error handling that: {type(exc).__name__}: {exc}")
            else:
                self.log(f"ignored update {update.id} from {update.ignored}")
            if update.cursor is not None:
                self.save_offset(update.cursor)

    def run(self) -> None:
        self.log("Approval bot listening (Ctrl-C to stop)")
        self.recover()
        while not self.stopping:
            db.set_state(self.conn, "heartbeat", db.now_iso())  # reelline/pipeline/run_daily.py watches it
            try:
                self.poll_once(conf().review.approve.poll_timeout_s)
            except NotifyError as exc:
                self.log(f"receive failed: {exc}; retrying in {conf().review.approve.error_backoff_s}s")
                time.sleep(conf().review.approve.error_backoff_s)
            try:
                self.tick()
            except Exception:
                self.log(f"tick failed:\n{traceback.format_exc()}")
        self.log("Approval bot stopped")
