"""The bot's publishing taps and tick."""

from __future__ import annotations

import sqlite3

from reelline.pipeline.approve_bot.base import (
    ACTIVE,
    hm,
)
from reelline.pipeline.approve_bot.jobs import (
    active_job,
    add_job,
    publish_job,
)
from reelline.pipeline.approve_bot.reporting import (
    reopen_review,
)
from reelline.pipeline.approve_bot.state import BotState
from reelline.review import machine as review
from reelline.review.base import Tap


class PublishMixin(BotState):
    def publish_tick(self, retried: int | None = None) -> None:
        """Start the publish jobs that are due; check the posts whose time has come.
        `retried`: a reel whose Retry was just tapped, started even after a failed job."""
        from reelline.pipeline import publish

        now = self.now()
        added = False
        for reel_id in publish.due(self.conn, now):
            reel = self.conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()
            pick = self.pick(reel["pick_id"])
            last = self.conn.execute(
                "SELECT status FROM jobs WHERE kind = 'publish' AND pick_id = ? ORDER BY id DESC LIMIT 1", (pick["id"],)
            ).fetchone()
            if last is not None and (last["status"] in ACTIVE or (last["status"] == "failed" and reel_id != retried)):
                continue  # running, or failed as a job: its own Retry button, never an automatic loop
            r = review.for_reel(self.conn, reel_id)
            job_id = add_job(self.conn, "publish", pick, review_id=r["id"] if r else None, detail={"reel_id": reel_id})
            if job_id is not None:
                added = True
                self.log(f"Reel #{reel_id}: publish job #{job_id}")
        if added:
            self.dispatch()
        publish.poll(
            self.conn, self.publish_clients, now, send=lambda text, markup: self.say(text, markup), log=self.log
        )

    def on_publish_retry(self, query: Tap, data: str) -> None:
        """A publish Retry: one channel (p:<publication>:rt) or every failed one of the
        reel (p:<reel>:ra), back to waiting, then one publish job for them. While the
        reel's publish job is queued or running: "already running", nothing changes."""
        from reelline.pipeline import publish

        parts = data.split(":")
        if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ("rt", "ra"):
            self.answer(query, "Unknown button")
            return
        if parts[2] == "rt":
            p = publish.get_pub(self.conn, int(parts[1]))
            if p is None:
                self.answer(query, "expired")
                return
            reel_id, what = p["reel_id"], p["channel"]
        else:
            reel_id, what = int(parts[1]), "all"
        if publish_job(self.conn, reel_id) is not None:
            self.answer(query, "already running")
            return
        result = publish.retry(self.conn, int(parts[1])) if parts[2] == "rt" else publish.retry_all(self.conn, reel_id)
        self.answer(query, "⏳ retrying..." if result == "retrying" else result)
        if result == "retrying":
            self.log(f"Reel #{reel_id}: retry {what} by {query.user}")
            self.publish_tick(retried=reel_id)

    def on_comment_retry(self, query: Tap, data: str) -> None:
        """A failed links comment's Retry (reelline/pipeline/comments.py): pending again, posted on
        this publish round."""
        from reelline.pipeline import comments

        parts = data.split(":")
        if len(parts) != 3 or not parts[1].isdigit() or parts[2] != "rt":
            self.answer(query, "Unknown button")
            return
        result = comments.retry(self.conn, int(parts[1]))
        self.answer(query, "⏳ retrying..." if result == "retrying" else result)
        if result == "retrying":
            self.log(f"Comment retry for publication #{parts[1]} by {query.user}")
            self.publish_tick()

    def on_unschedule(self, query: Tap, r: sqlite3.Row, by: str) -> None:
        """Back to review: DB only while nothing went out, else a worker job deletes the
        posts and the R2 objects first."""
        from reelline.pipeline import publish

        now = self.now()
        reel = self.conn.execute("SELECT * FROM reels WHERE id = ?", (r["reel_id"],)).fetchone()
        why = "not approved" if r["status"] != "approved" else publish.can_unschedule(self.conn, reel, now)
        if why:
            self.answer(query, why)
            self.refresh(r["id"])
            return
        pick = self.pick(r["pick_id"])
        if active_job(self.conn, pick["slot_date"], pick["slot_time"]) is not None:
            self.answer(query, "a job is running for this slot; try again in a moment")
            return
        if not publish.needs_network(self.conn, reel["id"]):
            publish.unschedule(self.conn, reel["id"], None, now, log=self.log)
            reopen_review(self.conn, self.tg, r["id"], by, now, self.log)
            self.answer(query, "unscheduled")
            self.log(f"Reel #{reel['id']} unscheduled by {by} (nothing had gone out)")
            return
        job_id = add_job(self.conn, "unschedule", pick, review_id=r["id"], detail={"reel_id": reel["id"], "by": by})
        self.refresh(r["id"], line=f"↩️ unscheduling {hm(now)} (job #{job_id})...")
        self.answer(query, "⏳ unscheduling...")
        self.dispatch()
