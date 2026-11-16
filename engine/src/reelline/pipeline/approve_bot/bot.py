"""The approval bot class: set-up and events; its handlers live in the mixins."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime

from reelline.notify.base import NotifyError
from reelline.pipeline import db
from reelline.pipeline.approve_bot.base import (
    HELP,
    PAUSED_TEXT,
    RESUMED_TEXT,
    utcnow,
)
from reelline.pipeline.approve_bot.caption import CaptionMixin
from reelline.pipeline.approve_bot.jobs import (
    kill_worker,
    process_alive,
    spawn_process,
)
from reelline.pipeline.approve_bot.lifecycle import LifecycleMixin
from reelline.pipeline.approve_bot.publish_taps import PublishMixin
from reelline.pipeline.approve_bot.reports import ReportsMixin
from reelline.pipeline.approve_bot.review_taps import ReviewTapsMixin
from reelline.pipeline.scoring import slot_datetime
from reelline.review import machine as review
from reelline.review.base import Command, Tap, Text


class ApproveBot(ReviewTapsMixin, CaptionMixin, PublishMixin, ReportsMixin, LifecycleMixin):
    def __init__(
        self,
        conn: sqlite3.Connection,
        tg,
        *,
        source_names: dict[str, str],
        now: Callable[[], datetime] = utcnow,
        spawn: Callable[[int], object] = spawn_process,
        alive: Callable[[int | None, int], bool] = process_alive,
        picker_factory: Callable | None = None,
        log: Callable[[str], None] = lambda m: print(m, flush=True),
        publish_clients: Callable[[int], object] | None = None,
        kill: Callable[[int], None] = kill_worker,
    ):
        from reelline.pipeline.pick import Picker

        self.conn, self.names, self.now, self._spawn, self.alive, self.log = (
            conn,
            source_names,
            now,
            spawn,
            alive,
            log,
        )
        self.tg = tg
        self.kill = kill
        self.children: dict[int, object] = {}
        self.picker = (picker_factory or Picker)(
            conn,
            source_names=source_names,
            channel_factory=lambda: self.tg,
            now=now,
            on_picked=self.on_picked,
            on_none=self.on_menu_none,
            log=log,
        )
        self.stopping = False
        self.none_job: int | None = None
        from reelline.pipeline import publish

        self.publish_clients = publish_clients or (lambda reel_id: publish.real_clients(conn, reel_id))

    # -- small helpers --

    @property
    def tg(self):
        """The review channel (reelline/review/registry.py: a raw client is wrapped by review.channel's adapter)."""
        return self._channel

    @tg.setter
    def tg(self, given) -> None:
        self._channel = review.channel(given)

    def say(self, text: str, markup: list | None = None, reply_to=None):
        try:
            return self.tg.send(text, markup, reply_to=reply_to)
        except NotifyError as exc:
            self.log(f"send failed: {exc}")
            return None

    def answer(self, query: Tap, text: str) -> None:
        try:
            self.tg.ack(query, text)
        except NotifyError as exc:
            self.log(f"ack failed: {exc}")

    def refresh(self, review_id: int, **kw) -> sqlite3.Row:
        return review.refresh(self.conn, self.tg, review_id, log=self.log, now=self.now(), **kw)

    def pick(self, pick_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone()

    def slot_dt(self, pick: sqlite3.Row) -> datetime:
        return slot_datetime(date.fromisoformat(pick["slot_date"]), pick["slot_time"])

    # -- updates --

    def handle(self, update) -> None:
        """One update of the review channel (its raw form, or an event): its events handled."""
        for event in self.tg.parse(update):
            self.on_event(event)

    def on_event(self, event) -> None:
        if isinstance(event, Tap):
            query = event
            data = event.data
            if data.startswith("pick:"):
                self.none_job = None
                self.picker.handle_tap(query)
                if self.none_job is not None:  # None of these queued a pick job (committed by now)
                    self.dispatch()
                return
            if data.startswith("c:"):
                self.on_costs_tap(query, data)
                return
            if data.startswith("a:"):
                self.on_analytics_tap(query, data)
                return
            if data.startswith("p:"):
                self.on_publish_retry(query, data)
                return
            if data.startswith("pc:"):
                self.on_comment_retry(query, data)
                return
            parsed = review.parse_callback(data)
            if parsed is None:
                self.answer(query, "Unknown button")
            elif parsed[0] == "r":
                self.on_review_tap(query, parsed[1], parsed[2])
            else:
                self.on_retry(query, parsed[1])
            return
        if isinstance(event, Text):
            self.on_caption_text(event.user, event.text)
            return
        if not isinstance(event, Command):
            return
        command, args = f"/{event.name}", list(event.args)
        if command == "/status":
            self.say(self.status_text())
        elif command in ("/pause", "/resume"):
            db.set_state(self.conn, "paused", db.now_iso(self.now()) if command == "/pause" else None)
            self.say(PAUSED_TEXT if command == "/pause" else RESUMED_TEXT)
            self.log(f"{command[1:]}d by {event.user}")
        elif command == "/costs":
            self.on_costs_command(args)
        elif command == "/analytics":
            self.on_analytics_command(args)
        elif command in ("/help", "/start"):
            self.say(HELP)
        elif command == "/cancel":
            self.cancel_caption()
        else:
            self.say(f"Unknown command {command}. /help")
