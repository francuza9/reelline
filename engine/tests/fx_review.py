"""Shared pytest fixtures and their helpers (registered by conftest.py)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from reelline.config import conf
from reelline.pipeline import approve_bot as ab
from reelline.pipeline import db
from reelline.pipeline.produce import produce
from reelline.pipeline.qa import Report
from reelline.pipeline.scoring import slot_datetime
from reelline.review import machine as review
from tests.test_produce import NAMES, FakeChecker, FakeRenderer

CHAT = "4242"


SLOT = slot_datetime(datetime(2026, 10, 1).date(), "13:00")  # 09:00 UTC


BEFORE = SLOT - timedelta(hours=3)  # 10:00 Tbilisi


class FakeTg:
    """TelegramBot's surface: records every call, serves scripted getUpdates batches."""

    def __init__(self):
        self.sent, self.edits, self.answers, self.markups = [], [], [], []
        self.updates: list[list[dict]] = []
        self.next_id = 500
        self.chat_id = CHAT

    def send_message(self, text, markup=None, reply_to=None):
        self.next_id += 1
        self.sent.append(SimpleNamespace(id=self.next_id, text=text, markup=markup, reply_to=reply_to))
        return {"message_id": self.next_id}

    def edit_message(self, message_id, text, markup=None):
        self.edits.append(SimpleNamespace(id=message_id, text=text, markup=markup or {"inline_keyboard": []}))

    def edit_markup(self, message_id, markup):
        self.markups.append((message_id, markup))

    def answer_callback(self, callback_id, text=None):
        self.answers.append(text)

    def get_updates(self, offset, timeout):
        return self.updates.pop(0) if self.updates else []

    def from_our_chat(self, update):
        message = (update.get("callback_query") or {}).get("message") or update.get("message") or {}
        return str((message.get("chat") or {}).get("id")) == CHAT

    # helpers
    def last_edit(self, message_id):
        return [e for e in self.edits if e.id == message_id][-1]

    def buttons(self, markup):
        return [b["text"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


class TgReview:
    """produce's review channel over FakeTg (the real review.send())."""

    def __init__(self, tg):
        self.tg = tg

    def send_reel(self, mp4, header):
        self.tg.send_message(f"[video] {header}")

    def send_text(self, text):
        self.tg.send_message(text)

    def send_review(self, conn, reel_id, text):
        return review.send(conn, self.tg, reel_id, text)


class Spawner:
    def __init__(self):
        self.started = []

    def __call__(self, job_id):
        self.started.append(job_id)
        return SimpleNamespace(pid=90000 + job_id, poll=lambda: None)


def make_reel(w, tg, report=None, rows=()):
    result = produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=FakeRenderer(rows=rows),
        review_factory=lambda: TgReview(tg),
        log=lambda m: None,
        checker_factory=lambda: FakeChecker(report or Report([])),
    )
    return result.reel_id


@pytest.fixture
def b(ready, monkeypatch):
    """A rendered reel for 2026-10-01 13:00 with its review sent, and a bot at 10:00 Tbilisi."""
    monkeypatch.setattr(conf().review, "channel", "telegram")  # the bot is driven through the Telegram fake
    w = ready
    tg = FakeTg()
    w.reel_id = make_reel(w, tg)
    review.update(w.conn, review.for_reel(w.conn, w.reel_id)["id"], sent_at=db.now_iso(BEFORE))  # on the fake clock
    w.review = review.for_reel(w.conn, w.reel_id)
    clock = {"now": BEFORE}
    spawner = Spawner()
    alive = {}
    w.bot = ab.ApproveBot(
        w.conn,
        tg,
        source_names=NAMES,
        now=lambda: clock["now"],
        spawn=spawner,
        alive=lambda pid, job_id: alive.get(job_id, False),
        log=lambda m: None,
    )
    w.tg, w.clock, w.spawner, w.alive = tg, clock, spawner, alive
    return w
