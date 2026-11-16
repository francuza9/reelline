"""Constants, texts and small helpers shared by the bot and the worker."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.pipeline.scoring import tbilisi

JOB_KINDS = (
    "new_script",
    "new_voice",
    "new_images",
    "other_story",
    "script_produce",
    "publish",
    "unschedule",
    "pick",
    "qa_review",
)
JOB_LABELS = {
    "new_script": "new script",
    "new_voice": "new voice",
    "new_images": "new images",
    "other_story": "other story",
    "script_produce": "script and reel",
    "publish": "publish",
    "unschedule": "unschedule",
    "pick": "pick, script and reel",
    "qa_review": "QA and review",
}
ACTIVE = ("queued", "running")
# A dead worker's job error (reelline/pipeline/run_daily.py DIED: the tick resumes a chain job
# whose error starts with it).
WORKER_DIED = "the worker process died"
# Kinds the tick resumes when their worker dies (reelline/pipeline/run_daily.py CHAIN_KINDS); a
# hung job of another kind gets its failure report (Retry) instead.
CHAIN_KINDS = ("pick", "script_produce")
HASHTAG = re.compile(r"#\w+")

HELP = f"""{conf().channel.shown_name()} approval bot
/status - today's and tomorrow's slots and any running job
/costs [YYYY-MM] - the month's API costs (buttons without a month)
/analytics [reel_id] - views, engagement, watch time (buttons without a reel)
/pause - the daily schedule starts nothing new (reviews and approved reels go on)
/resume - the daily schedule runs again
/cancel - abort a caption edit
/help - this

Review buttons: Approve, Caption (send the new hook; a last line of only hashtags replaces the hashtags),
Regenerate (new script / voice / images, other story), Reject (a reason, then other story or skip the slot)."""


PAUSED_TEXT = (
    "⏸ Paused: the daily schedule starts nothing new. Reviews, approvals and approved reels go on. /resume to undo."
)
RESUMED_TEXT = "▶️ Resumed: the daily schedule runs again."


class JobError(ReellineError, RuntimeError):
    """A worker step that failed (the message says which)."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def hm(at: datetime) -> str:
    return at.astimezone(tbilisi()).strftime("%H:%M")
