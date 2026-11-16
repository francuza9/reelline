"""Reel reviews, channel-neutral: the reviews table, the state machine, the button sets and message edits.

produce sends a reel's review (reelline/pipeline/produce.py) and send() records it: the
status block's last message carries the buttons, and its id and text are stored, so
the bot (reelline/pipeline/approve_bot.py) and its worker jobs can edit it later, from any
process, after any restart. A review's `status` is pending until a decision
(approved, rejected) or until it's superseded or expired; while pending, `state` says
which buttons show:

  main          Approve, 🚀 Post now, Caption, Regenerate, Reject (no Regenerate once
                the slot has had review.approve.max_regenerations)
  confirm_now   Post now to all N (the enabled channels)? Confirm / Cancel: approved
                and published at once (the reel's QA failures in the question)
  confirm_qa    QA failed: Approve anyway? Yes / No
  confirm_late  the slot has passed: Post now / Next free slot / Back
  regen         New script, New voice, New images, Other story, Back
  reject        the reasons, Back
  reject_next   Other story (while regenerations are left) / Skip slot / Back
  caption       waiting for the new caption text (no buttons)
  working       a regeneration job is running (no buttons)

An approved review keeps one button, Unschedule, until publish.unschedule_until_min
before its slot (not for Post now): it takes the reel back to review
(reelline/pipeline/publish.py unschedule()).

Action data (a button's; Telegram's callback_data): "r:<review id>:<code>" (CODES), unchanged
since the first reviews, so buttons on messages sent long ago still parse. Every edit re-renders
the message: the stored text, then the result lines added so far. The buttons are neutral rows of
(label, action data); the review channel (reelline/review/base.py) renders them.

Every new review (a reel's first, a regeneration's, the one after a caption edit)
starts with header_text(): "🎬 Reel #<id> · <date> <slot> · <short title>" between
two rules (review.telegram.header).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC

from reelline.config import conf
from reelline.notify.base import Buttons, NotifyError, buttons, split_text
from reelline.notify.text import framed
from reelline.pipeline import db
from reelline.review.base import ReviewInfo

REGEN_KINDS = ("new_script", "new_voice", "new_images", "other_story")
REJECT_REASONS = ("facts_wrong", "jokes_weak", "story_boring", "render_voice", "other")

# Button code -> action. Kept to two letters so "r:<id>:<code>" stays far under 64 bytes.
CODES = {
    "ap": "approve",
    "ca": "caption",
    "rg": "regenerate",
    "rj": "reject",
    "qy": "qa_yes",
    "qn": "qa_no",
    "pn": "post_now",
    "nf": "next_slot",
    "bk": "back",
    "gs": "new_script",
    "gv": "new_voice",
    "gi": "new_images",
    "go": "other_story",
    "xf": "facts_wrong",
    "xj": "jokes_weak",
    "xs": "story_boring",
    "xr": "render_voice",
    "xo": "other",
    "jo": "reject_other_story",
    "js": "skip_slot",
    "un": "unschedule",
    "pq": "post_now_ask",
}
CODE_OF = {action: code for code, action in CODES.items()}
# The actions each state's buttons offer (a tap on anything else is a stale button).
STATE_ACTIONS = {
    "main": ("approve", "post_now_ask", "caption", "regenerate", "reject"),
    "confirm_now": ("post_now", "back"),
    "confirm_qa": ("qa_yes", "qa_no"),
    "confirm_late": ("post_now", "next_slot", "back"),
    "regen": ("new_script", "new_voice", "new_images", "other_story", "back"),
    "reject": (*REJECT_REASONS, "back"),
    "reject_next": ("reject_other_story", "skip_slot", "back"),
    "caption": (),
    "working": (),
}


def parse_callback(data: str) -> tuple[str, int, str] | None:
    """("r" | "j", id, action-or-code) for our callback data, None for anything else
    (pick.py's "pick:..." menus included)."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] not in ("r", "j") or not parts[1].isdigit():
        return None
    if parts[0] == "r":
        return ("r", int(parts[1]), CODES[parts[2]]) if parts[2] in CODES else None
    return ("j", int(parts[1]), parts[2]) if parts[2] == "rt" else None


def get(conn: sqlite3.Connection, review_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()


def for_reel(conn: sqlite3.Connection, reel_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reviews WHERE reel_id = ? ORDER BY id DESC LIMIT 1", (reel_id,)).fetchone()


def regen_count(conn: sqlite3.Connection, slot_date: str, slot_time: str) -> int:
    marks = ", ".join("?" * len(REGEN_KINDS))
    return conn.execute(
        f"SELECT COUNT(*) FROM jobs WHERE slot_date = ? AND slot_time = ? AND kind IN ({marks})",
        (slot_date, slot_time, *REGEN_KINDS),
    ).fetchone()[0]


def regen_left(conn: sqlite3.Connection, review: sqlite3.Row) -> int:
    pick = conn.execute("SELECT slot_date, slot_time FROM picks WHERE id = ?", (review["pick_id"],)).fetchone()
    return max(0, conf().review.approve.max_regenerations - regen_count(conn, pick["slot_date"], pick["slot_time"]))


def markup(conn: sqlite3.Connection, review: sqlite3.Row, now=None) -> Buttons:
    """The buttons for the review's state (none once it isn't pending, but Unschedule
    on an approved one while it may still be unscheduled)."""
    if review["status"] == "approved":
        from datetime import datetime

        from reelline.pipeline import publish

        reel = conn.execute("SELECT * FROM reels WHERE id = ?", (review["reel_id"],)).fetchone()
        if reel is not None and publish.can_unschedule(conn, reel, now or datetime.now(UTC)) is None:
            return buttons([[(conf().publish.buttons["unschedule"], f"r:{review['id']}:{CODE_OF['unschedule']}")]])
        return []
    if review["status"] != "pending":
        return []
    label = conf().review.approve.labels

    def b(action: str, text: str | None = None):
        return (text or label[action], f"r:{review['id']}:{CODE_OF[action]}")

    state, left = review["state"], regen_left(conn, review)
    if state == "main":
        rows = [[b("approve"), b("post_now_ask")], [b("caption")] + ([b("regenerate")] if left else []) + [b("reject")]]
    elif state == "confirm_now":
        rows = [[b("post_now", label["confirm"]), b("back", label["cancel"])]]
    elif state == "confirm_qa":
        rows = [[b("qa_yes", label["yes"]), b("qa_no", label["no"])]]
    elif state == "confirm_late":
        rows = [[b("post_now"), b("next_slot")], [b("back")]]
    elif state == "regen":
        rows = [[b("new_script"), b("new_voice")], [b("new_images"), b("other_story")], [b("back")]]
    elif state == "reject":
        rows = [[b("facts_wrong"), b("jokes_weak")], [b("story_boring"), b("render_voice")], [b("other"), b("back")]]
    elif state == "reject_next":
        rows = [([b("reject_other_story", label["other_story"])] if left else []) + [b("skip_slot")], [b("back")]]
    else:
        rows = []
    return buttons(rows)


def local_hm(iso: str | None) -> str:
    """An ISO timestamp as Tbilisi HH:MM."""
    from datetime import datetime

    from reelline.pipeline.scoring import tbilisi

    return datetime.fromisoformat(iso).astimezone(tbilisi()).strftime("%H:%M") if iso else "?"


def prompt(conn: sqlite3.Connection, review: sqlite3.Row) -> str | None:
    """The question the state's buttons answer (shown under the result lines, gone once answered)."""
    from datetime import datetime, timedelta

    state = review["state"]
    pick = conn.execute("SELECT * FROM picks WHERE id = ?", (review["pick_id"],)).fetchone()
    slot = f"{pick['slot_time']} slot ({pick['slot_date']})"
    if state == "confirm_qa":
        reel = conn.execute("SELECT qa_failures FROM reels WHERE id = ?", (review["reel_id"],)).fetchone()
        return f"QA failed: {'; '.join(json.loads(reel['qa_failures'] or '[]'))}. Approve anyway?"
    if state == "confirm_late":
        return f"The {slot} has passed. Post now, or move it to the next free slot?"
    if state == "confirm_now":
        channels = [c for c, on in conf().publish.channels.items() if on]
        reel = conn.execute("SELECT qa_status, qa_failures FROM reels WHERE id = ?", (review["reel_id"],)).fetchone()
        qa = (
            f" QA failed: {'; '.join(json.loads(reel['qa_failures'] or '[]'))}."
            if reel is not None and reel["qa_status"] == "fail"
            else ""
        )
        return f"🚀 Post now to all {len(channels)}?{qa}"
    if state == "regen":
        return f"🔁 Regenerate how? ({regen_left(conn, review)} of {conf().review.approve.max_regenerations} left for this slot)"
    if state == "reject":
        return "❌ Why reject?"
    if state == "reject_next":
        return f"❌ {conf().review.approve.labels[review['reject_reason']]}. Another story, or skip the {slot}?"
    if state == "caption":
        until = (
            datetime.fromisoformat(review["caption_requested_at"])
            + timedelta(minutes=conf().review.approve.caption_edit_timeout_min)
        ).isoformat()
        return f"✏️ Waiting for the new caption until {local_hm(until)}; /cancel aborts."
    return None


def text_of(conn: sqlite3.Connection, review: sqlite3.Row, limit: int | None = None) -> str:
    """The message text: the stored text, the result lines, the state's question (the
    text is cut so the whole fits the channel's limit; default: the review chat's)."""
    lines = json.loads(review["lines"] or "[]")
    if review["status"] == "pending":
        lines = lines + [q for q in [prompt(conn, review)] if q]
    tail = ("\n\n" + "\n".join(lines)) if lines else ""
    head = review["text"]
    room = (limit or conf().review.telegram.text_max_chars) - len(tail)
    if len(head) > room:
        head = head[: max(0, room - 4)] + " ..."
    return head + tail


def short_title(conn: sqlite3.Connection, reel: sqlite3.Row) -> str:
    """The reel's story in a few words: its caption's YouTube title, else the story's
    title, cut to review.telegram.header["title_chars"]."""
    row = conn.execute(
        "SELECT youtube_title FROM captions WHERE script_id = ? ORDER BY id DESC LIMIT 1", (reel["script_id"],)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT s.title FROM picks p JOIN stories s ON s.id = p.story_id WHERE p.id = ?", (reel["pick_id"],)
        ).fetchone()
    title = " ".join((row[0] if row else reel["name"]).split())
    n = conf().review.telegram.header["title_chars"]
    return title if len(title) <= n else title[: n - 1].rstrip() + "…"


def header_text(conn: sqlite3.Connection, reel_id: int) -> str:
    """The header message sent before a reel's review."""
    reel = conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()
    pick = conn.execute("SELECT slot_date, slot_time FROM picks WHERE id = ?", (reel["pick_id"],)).fetchone()
    return framed(
        conf()
        .review.telegram.header["review"]
        .format(reel_id=reel_id, date=pick["slot_date"], slot=pick["slot_time"], title=short_title(conn, reel))
    )


def create(conn: sqlite3.Connection, reel_id: int, text: str) -> int:
    reel = conn.execute("SELECT pick_id FROM reels WHERE id = ?", (reel_id,)).fetchone()
    now = db.now_iso()
    with db.transaction(conn):
        return conn.execute(
            """INSERT INTO reviews (reel_id, pick_id, status, state, text, sent_at, updated_at)
               VALUES (?, ?, 'pending', 'main', ?, ?, ?)""",
            (reel_id, reel["pick_id"], text, now, now),
        ).lastrowid


def update(conn: sqlite3.Connection, review_id: int, **fields) -> None:
    if "status" in fields and fields["status"] not in db.REVIEW_STATUSES:
        raise db.DbError(f"Unknown review status {fields['status']!r}")
    fields["updated_at"] = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            f"UPDATE reviews SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), review_id)
        )


def channel(given=None):
    """The review channel for `given` (reelline/review/registry.py: a channel as it is, a raw
    client wrapped by the configured adapter, None: the configured one)."""
    from reelline.review.registry import get_review_channel

    return get_review_channel(given)


def info(conn: sqlite3.Connection, review_id: int) -> ReviewInfo:
    """What a structured channel shows with the status block: the slot, caption, video, flags."""
    from reelline.paths import stored_path

    r = get(conn, review_id)
    reel = conn.execute("SELECT * FROM reels WHERE id = ?", (r["reel_id"],)).fetchone()
    pick = conn.execute("SELECT slot_date, slot_time FROM picks WHERE id = ?", (r["pick_id"],)).fetchone()
    script = conn.execute("SELECT failures FROM scripts WHERE id = ?", (reel["script_id"],)).fetchone()
    flags = {
        "factcheck": json.loads((script["failures"] if script else None) or "[]"),
        "qa": json.loads(reel["qa_failures"] or "[]") if reel["qa_status"] == "fail" else [],
    }
    return ReviewInfo(
        r["id"],
        reel["id"],
        pick["slot_date"],
        pick["slot_time"],
        reel["caption"],
        stored_path(reel["mp4_path"]) if reel["mp4_path"] else None,
        {k: v for k, v in flags.items() if v},
    )


def send(conn: sqlite3.Connection, bot, reel_id: int, text: str) -> int:
    """Send the review's status block (the buttons on its last message) and record it.
    Returns the review id."""
    ch = channel(bot)
    chunks = split_text(text, ch.text_limit)
    for chunk in chunks[:-1]:
        ch.send(chunk)
    review_id = create(conn, reel_id, chunks[-1])
    ref = ch.send_review(chunks[-1], markup(conn, get(conn, review_id)), info(conn, review_id))
    update(conn, review_id, message_id=ref)
    return review_id


def resend(conn: sqlite3.Connection, bot, review_id: int, text: str) -> None:
    """Send the review again as a new status block (after a caption edit): the same
    review row, now on the new message with the main buttons; result lines start over."""
    ch = channel(bot)
    chunks = split_text(text, ch.text_limit)
    for chunk in chunks[:-1]:
        ch.send(chunk)
    update(conn, review_id, text=chunks[-1], lines="[]", state="main", caption_requested_at=None)
    ref = ch.send_review(chunks[-1], markup(conn, get(conn, review_id)), info(conn, review_id))
    update(conn, review_id, message_id=ref)


def refresh(
    conn: sqlite3.Connection,
    bot,
    review_id: int,
    *,
    line: str | None = None,
    log: Callable[[str], None] = print,
    now=None,
    **fields,
) -> sqlite3.Row:
    """Apply `fields` (status, state, ...), add a result `line`, and edit the message to
    match. A failed edit (the message is gone, too old to edit) is logged, not raised:
    the database is the record."""
    ch = channel(bot)
    if line is not None:
        lines = json.loads(get(conn, review_id)["lines"] or "[]")
        fields["lines"] = json.dumps(lines + [line], ensure_ascii=False)
    if fields:
        update(conn, review_id, **fields)
    review = get(conn, review_id)
    if review["message_id"] is not None:
        try:
            ch.edit(review["message_id"], text_of(conn, review, ch.text_limit), markup(conn, review, now))
        except NotifyError as exc:
            if "not modified" not in str(exc):
                log(f"review #{review_id}: edit failed: {exc}")
    return review
