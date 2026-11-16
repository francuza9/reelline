"""The review decisions, channel-neutral: reelline/review/machine.py and reelline/pipeline/approve_bot.py
driven through a FakeReviewChannel (reelline/review/fakes.py): Tap / Command / Text events in,
neutral buttons (label, action data) out. No Telegram anywhere: the same state machine,
idempotency, double-tap safety, expiry and regeneration limit as tests/test_approve_bot.py sees
through the Telegram adapter."""

from datetime import datetime, timedelta

import pytest
from reelline.config import conf
from reelline.pipeline import approve_bot as ab
from reelline.pipeline import db
from reelline.pipeline.produce import Review, produce
from reelline.pipeline.qa import Check, Report
from reelline.review import Command, Incoming, Tap, Text
from reelline.review import machine as review
from reelline.review.fakes import FakeReviewChannel
from tests.fx_review import BEFORE, SLOT, Spawner
from tests.test_produce import NAMES, FakeChecker, FakeRenderer

MAIN = ["✅ Approve", "🚀 Post now", "✏️ Caption", "🔁 Regenerate", "❌ Reject"]


def make_reel(w, ch, report=None):
    return produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=FakeRenderer(),
        review_factory=lambda: Review(ch),
        log=lambda m: None,
        checker_factory=lambda: FakeChecker(report or Report([])),
    ).reel_id


@pytest.fixture
def n(ready):
    w = ready
    w.ch = FakeReviewChannel()
    w.reel_id = make_reel(w, w.ch)
    review.update(w.conn, review.for_reel(w.conn, w.reel_id)["id"], sent_at=db.now_iso(BEFORE))
    w.review = review.for_reel(w.conn, w.reel_id)
    w.clock = {"now": BEFORE}
    w.logs = []
    w.bot = ab.ApproveBot(
        w.conn,
        w.ch,
        source_names=NAMES,
        now=lambda: w.clock["now"],
        spawn=Spawner(),
        alive=lambda pid, job_id: False,
        log=w.logs.append,
    )
    return w


def press(w, review_id, action, user="ana"):
    w.bot.on_event(Tap(f"r:{review_id}:{review.CODE_OF[action]}", user, w.review["message_id"]))
    return w.ch.last_ack()


def shown(w):
    return w.ch.message(w.review["message_id"])


def test_the_review_goes_out_structured_with_neutral_buttons(n):
    ref, info = n.ch.reviews[-1]
    assert ref == n.review["message_id"] and (info.review_id, info.reel_id) == (n.review["id"], n.reel_id)
    assert (info.slot_date, info.slot_time) == ("2026-10-01", "13:00") and info.caption and info.flags == {}
    assert n.ch.videos and n.ch.videos[0][1].startswith(f"Reel #{n.reel_id} · ")
    assert n.ch.labels(shown(n).buttons) == MAIN
    assert [d for row in shown(n).buttons for _, d in row] == [
        f"r:{n.review['id']}:{c}" for c in ("ap", "pq", "ca", "rg", "rj")
    ]


def test_failed_checks_are_flags_of_the_review(ready):
    ch = FakeReviewChannel()
    fail = Report([Check("visual", "frame 2", "fail", None, None, ["frame 2: captions overlap"])])
    make_reel(ready, ch, fail)
    assert ch.reviews[-1][1].flags == {"qa": ["visual: frame 2: captions overlap"]}


def test_approve_once_then_every_tap_is_idempotent(n):
    assert press(n, n.review["id"], "approve") == "approved"
    r = review.get(n.conn, n.review["id"])
    assert (r["status"], r["decided_by"]) == ("approved", "ana")
    assert shown(n).text.endswith("✅ approved 10:00 - 13:00 slot") and n.ch.labels(shown(n).buttons) == [
        "↩️ Unschedule"
    ]
    sent = len(n.ch.sent)
    assert press(n, n.review["id"], "approve") == "already approved"  # a double tap
    assert press(n, n.review["id"], "reject") == "already approved"
    assert len(n.ch.sent) == sent and review.get(n.conn, n.review["id"])["status"] == "approved"


def test_a_stale_action_is_expired_and_resyncs_the_buttons(n):
    assert press(n, n.review["id"], "skip_slot") == "expired"  # reject_next isn't open
    assert review.get(n.conn, n.review["id"])["state"] == "main" and n.ch.labels(shown(n).buttons) == MAIN
    n.bot.on_event(Tap("r:999999:ap", "ana"))
    assert n.ch.last_ack() == "expired"
    n.bot.on_event(Tap("zz:1", "ana"))
    assert n.ch.last_ack() == "Unknown button"


def test_regenerate_its_limit_and_a_second_tap_while_running(n):
    assert press(n, n.review["id"], "regenerate") == "Regenerate how?"
    assert n.ch.labels(shown(n).buttons) == ["New script", "New voice", "New images", "Other story", "Back"]
    assert press(n, n.review["id"], "new_voice") == "⏳ regenerating..."
    assert press(n, n.review["id"], "approve") == "already running"
    n.conn.execute("UPDATE jobs SET status = 'done'")
    pick = n.conn.execute("SELECT * FROM picks WHERE id = ?", (n.review["pick_id"],)).fetchone()
    for _ in range(conf().review.approve.max_regenerations - 1):
        ab.add_job(n.conn, "new_images", pick, exclusive=False)
    n.conn.execute("UPDATE jobs SET status = 'done'")
    review.refresh(n.conn, n.ch, n.review["id"], state="main")
    assert "🔁 Regenerate" not in n.ch.labels(shown(n).buttons)
    assert press(n, n.review["id"], "regenerate") == "regeneration limit reached"


def test_reject_needs_a_reason_then_skips_the_slot(n):
    assert press(n, n.review["id"], "reject") == "Why?"
    assert press(n, n.review["id"], "jokes_weak") == "Other story or skip?"
    assert press(n, n.review["id"], "skip_slot") == "rejected"
    assert review.get(n.conn, n.review["id"])["status"] == "rejected"
    assert n.conn.execute("SELECT status FROM picks WHERE id = ?", (n.review["pick_id"],)).fetchone()[0] == "skipped"


def test_a_review_expires_on_the_clock_and_its_buttons_go(n):
    n.clock["now"] = datetime.fromisoformat(n.review["sent_at"]) + timedelta(hours=24)
    n.bot.tick()
    r = review.get(n.conn, n.review["id"])
    assert r["status"] == "expired" and shown(n).buttons == [] and "⌛ expired" in shown(n).text
    assert press(n, r["id"], "approve") == "expired"


def test_operator_commands_are_channel_neutral(n):
    n.bot.on_event(Command("pause", (), "ana"))
    assert db.get_state(n.conn, "paused") and n.ch.sent[-1].text == ab.PAUSED_TEXT and "paused by ana" in n.logs
    n.bot.on_event(Command("resume", (), "ana"))
    assert db.get_state(n.conn, "paused") is None and n.ch.sent[-1].text == ab.RESUMED_TEXT
    n.bot.on_event(Command("status"))
    assert n.ch.sent[-1].text.startswith("Status ")
    n.bot.on_event(Command("costs"))
    assert n.ch.labels(n.ch.sent[-1].buttons) == list(conf().costs.buttons[k] for k in ("this", "last", "pick"))
    n.bot.on_event(Command("frobnicate"))
    assert n.ch.sent[-1].text == "Unknown command /frobnicate. /help"


def test_caption_edit_waits_for_text_and_cancel_aborts(n):
    assert press(n, n.review["id"], "caption") == "Send the new caption"
    assert review.get(n.conn, n.review["id"])["state"] == "caption" and shown(n).buttons == []
    n.bot.on_event(Command("cancel"))
    assert review.get(n.conn, n.review["id"])["state"] == "main"
    n.bot.on_event(Text("a stray line", "ana"))
    assert n.ch.sent[-1].text == "Nothing is waiting for a caption. /help"


def test_a_failed_job_gets_a_retry_action_and_the_retry_requeues_it(n):
    pick = n.conn.execute("SELECT * FROM picks WHERE id = ?", (n.review["pick_id"],)).fetchone()
    job_id = ab.add_job(n.conn, "new_voice", pick, n.review["id"])
    ab.update_job(n.conn, job_id, status="failed", error="boom")
    ab.report_failure(n.conn, n.ch, ab.get_job(n.conn, job_id), log=lambda m: None)
    report = n.ch.sent[-1]
    assert report.buttons == [[(conf().review.approve.labels["retry"], f"j:{job_id}:rt")]]
    assert ab.get_job(n.conn, job_id)["message_id"] == report.ref
    n.bot.on_event(Tap(f"j:{job_id}:rt", "ana", report.ref))
    assert n.ch.last_ack() == "⏳ retrying..." and ab.get_job(n.conn, job_id)["status"] == "queued"
    n.bot.on_event(Tap(f"j:{job_id}:rt", "ana", report.ref))
    assert n.ch.last_ack() == "already queued"


def test_poll_keeps_the_cursor_and_logs_ignored_updates(n):
    n.ch.incoming = [Incoming("7", [], "8", ignored="chat 99"), Incoming("8", [Command("help")], "c9")]
    n.bot.poll_once(0)
    assert "ignored update 7 from chat 99" in n.logs and n.ch.sent[-1].text.startswith(
        f"{conf().channel.shown_name()} approval bot"
    )
    assert n.bot.offset() == "c9"
    n.ch.incoming = [Incoming("9", [Command("help")], None)]  # a channel without a cursor
    n.bot.poll_once(0)
    assert n.bot.offset() == "c9"


def test_late_approval_asks_then_posts_now(n):
    n.clock["now"] = SLOT + timedelta(minutes=5)
    assert press(n, n.review["id"], "approve") == "The slot has passed"
    assert n.ch.labels(shown(n).buttons) == ["Post now", "Next free slot", "Back"]
    assert press(n, n.review["id"], "post_now") == "approved"
    reel = n.conn.execute("SELECT publish_mode FROM reels WHERE id = ?", (n.reel_id,)).fetchone()
    assert reel["publish_mode"] == "now"
