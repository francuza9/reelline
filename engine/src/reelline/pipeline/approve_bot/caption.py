"""Caption edits sent to the bot."""

from __future__ import annotations

import json
import sqlite3

from reelline.notify.base import NotifyError
from reelline.notify.text import about_reel
from reelline.paths import stored_path
from reelline.pipeline import db
from reelline.pipeline.approve_bot.base import (
    HASHTAG,
    hm,
)
from reelline.pipeline.approve_bot.state import BotState
from reelline.review import machine as review


class CaptionMixin(BotState):
    def waiting_caption(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM reviews WHERE status = 'pending' AND state = 'caption' "
            "ORDER BY caption_requested_at DESC LIMIT 1"
        ).fetchone()

    def cancel_caption(self) -> None:
        r = self.waiting_caption()
        if r is None:
            self.say("Nothing to cancel.")
            return
        self.refresh(r["id"], state="main", line=f"✏️ caption edit cancelled {hm(self.now())}")
        self.say(about_reel(r["reel_id"], "Caption edit cancelled."))

    def on_caption_text(self, user: str, text: str) -> None:
        r = self.waiting_caption()
        if r is None:
            self.say("Nothing is waiting for a caption. /help")
            return
        hook, hashtags = parse_caption_edit(text)
        if not hook:
            self.say(about_reel(r["reel_id"], "The caption is empty. Send it again, or /cancel."))
            return
        bad = [t for t in hashtags or [] if not HASHTAG.fullmatch(t)]
        if bad:
            self.say(about_reel(r["reel_id"], f"Not hashtags: {' '.join(bad)}. Send it again, or /cancel."))
            return
        if "\n" in hook and self.shows_images(r["reel_id"]):
            self.say(
                about_reel(
                    r["reel_id"],
                    "The hook must be one line: the reel shows images, and their credits "
                    "line stays the caption's 4th line. Send it again, or /cancel.",
                )
            )
            return
        self.apply_caption(r, hook, hashtags, user, text)

    def shows_images(self, reel_id: int) -> bool:
        """Does the reel show images? An unreadable images.json counts as yes (the
        stricter caption rule; QA's recheck reports the file)."""
        from reelline.pipeline import publish
        from reelline.pipeline.credits import CreditsError

        reel = self.conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()
        try:
            return bool(publish.shown_images(reel))
        except CreditsError:
            return True

    def apply_caption(self, r: sqlite3.Row, hook: str, hashtags: list[str] | None, by: str, raw: str) -> None:
        from reelline.pipeline import qa
        from reelline.pipeline.caption import assemble_caption, with_required_hashtags
        from reelline.pipeline.produce import status_block

        reel = self.conn.execute("SELECT * FROM reels WHERE id = ?", (r["reel_id"],)).fetchone()
        cap = self.conn.execute(
            "SELECT * FROM captions WHERE script_id = ? ORDER BY id DESC LIMIT 1", (reel["script_id"],)
        ).fetchone()
        tags, added = with_required_hashtags(hashtags if hashtags is not None else json.loads(cap["hashtags"]))
        with db.transaction(self.conn):
            self.conn.execute(
                """INSERT INTO captions (script_id, hook, youtube_title, hashtags, model, prompt_version, raw_json,
                       created_at) VALUES (?, ?, ?, ?, 'human', 0, ?, ?)""",
                (
                    reel["script_id"],
                    hook,
                    cap["youtube_title"],
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps({"by": by, "text": raw}, ensure_ascii=False),
                    db.now_iso(self.now()),
                ),
            )
        caption = assemble_caption(self.conn, reel["script_id"], self.names, reel["id"])
        path = stored_path(reel["qa_path"]) if reel["qa_path"] else None
        images = stored_path(reel["images_path"]) if reel["images_path"] else None
        report = qa.recheck_caption(path, caption, cap["youtube_title"], tags, reel["id"], images)
        fields = {"caption": caption, "updated_at": db.now_iso(self.now())}
        if reel["qa_status"] != "skipped":
            fields.update(
                qa_status=report.status,
                qa_failures=json.dumps(report.failures, ensure_ascii=False),
                qa_warnings=json.dumps(report.warnings, ensure_ascii=False),
            )
        with db.transaction(self.conn):
            self.conn.execute(
                f"UPDATE reels SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), reel["id"])
            )
        restored = f" ({' '.join(added)} added)" if added else ""
        self.refresh(r["id"], state="working", line=f"✏️ caption edited {hm(self.now())}{restored} - new review below")
        self.say(review.header_text(self.conn, reel["id"]))
        self.say(caption)
        reel = self.conn.execute("SELECT * FROM reels WHERE id = ?", (reel["id"],)).fetchone()
        try:
            review.resend(self.conn, self.tg, r["id"], status_block(self.conn, reel))
        except NotifyError as exc:
            self.log(f"review #{r['id']}: resend failed: {exc}; buttons back on the old message")
            self.refresh(r["id"], state="main")
        caption_failures = [f for f in report.failures if f.startswith("text: ")]
        self.log(
            f"Reel #{reel['id']}: caption edited by {by}{restored}; caption checks "
            f"{'failed: ' + '; '.join(caption_failures) if caption_failures else 'pass'}"
        )


def parse_caption_edit(text: str) -> tuple[str, list[str] | None]:
    """(hook, hashtags or None): a last line of only hashtag-like tokens (each starting
    with #) replaces the hashtags; the rest is the hook."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    last = lines[-1].split() if lines else []
    if last and all(t.startswith("#") for t in last):
        return "\n".join(lines[:-1]).strip(), last
    return "\n".join(lines).strip(), None
