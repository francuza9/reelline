"""The approval bot: the one listener of the review channel (review.channel, reelline/review/).

Usage:
  python -m reelline.pipeline.approve_bot
      Receive from the review channel (Telegram: getUpdates long polling, TELEGRAM_CHAT_ID
      only; anything else is logged and dropped) and handle its channel-neutral events
      (reelline/review/base.py): the review buttons, the caption edits, pick.py's contender
      menus, /status, /costs, /analytics, /pause, /resume, /help and /cancel. Runs
      until Ctrl-C / SIGTERM. The bot never sees the channel's own format: taps carry
      the button's action data, commands their name; replies and edits go back
      through the channel, which renders them.
  python -m reelline.pipeline.approve_bot job <job_id>
      Run one worker job (the bot starts these itself, detached).

Everything is in SQLite (reelline/review/machine.py for the reviews; jobs, rejections and
bot_state here), nothing in memory: after a restart the buttons on every message keep
working, open menus and pending reviews carry on, and the jobs are re-attached.
Every tap edits its message (buttons removed or changed, a result line added) and is
idempotent: a tap on a decided, superseded or stale message says so and changes
nothing. Numbers, timeouts and labels: review.approve in the channel config.

Review buttons (reelline/review/machine.py has the states):
  Approve     a QA-failed reel asks "Approve anyway?" first; before the slot the reel
              is scheduled for it, after it "Post now" / "Next free slot" (the nearest
              later slot without a live pick and whose chain isn't mid-pick; the pick
              moves there). The reel, its pick and script become approved, then
              reelline/pipeline/publish.py enqueue() (a dry run until step 7).
  Caption     the next text message within review.approve.caption_edit_timeout_min
              replaces the hook (a last line of only hashtags replaces the hashtags;
              a required one missing from it, steps.caption.required_hashtags, is appended);
              the source line, credits, AI line and YouTube title stay as assembled.
              A reel that shows images takes a one-line hook only (the credits line
              stays the caption's 4th line). QA's caption checks run again (the
              credits line among them), and the new caption and review are sent.
              /cancel aborts.
  Regenerate  New script (story + punch-up + fact-check from the same brief), New
              voice (fresh TTS, the tag stays pinned), New images (re-picked), Other
              story (this pick and its story dropped, the slot picked again with the
              pick rules, possibly a new menu); then produce. Each makes a new reel
              and review; the old ones are superseded. review.approve.max_regenerations
              per slot.
  Reject      a reason (logged in rejections), then Other story or Skip slot. Facts
              wrong and Story boring reject the story; the other reasons leave it
              eligible for a later slot (a skip keeps it off this one).

The heavy work runs in worker jobs (the jobs table), one detached process each
(`job <id>`, in its own session, so killing the bot doesn't kill it): at most one
active job per slot; a second tap says "already running". A job reports its result
itself (the new review) or its failure (the step and the error, with a Retry button),
and a job whose process died is reported as failed by the bot. Kinds: new_script,
new_voice, new_images, other_story, script_produce (after a menu answer, or a
re-pick: the brief, the script, the caption, then the reel), and pick (a slot's pick,
then on as script_produce; after a menu's "None of these", without a second menu).

Menus (reelline/pipeline/pick.py): Confirm picks the first chosen story, queues the rest and
dismisses the others for good; None of these dismisses them all and queues a pick job
for the best story outside the menu. The pick message names the story's title.

/costs: buttons This month / Last month / Pick month (the last costs.months_back);
/costs YYYY-MM goes straight to that month. The report (reelline/pipeline/costs.py; Tbilisi
months) comes with a CSV button that sends every cost row of the month as a file.

/analytics: buttons Last 7 days / Last 30 days / All time; /analytics <reel_id> for one
reel. The reports are reelline/pipeline/analytics.py's (read-only stats, reelline/pipeline/stats.py),
split only when over Telegram's limit.

Periodically (between long polls): menus past steps.pick.menu_timeout_min take their #1
(said in the chat); a review not acted on review.approve.reminder_before_slot_min before
its slot gets one reminder (a reply to it); one not acted on by expires_at()
(review.approve.expire_after_slot_h after its slot, at least review.approve.expire_after_h after
it was sent) is expired (never approved, never posted); a caption edit not
answered in time is dropped; dead jobs are reported.

Credentials: the review channel's (Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), the
model providers', all from .env.
"""

from __future__ import annotations

from reelline.pipeline.approve_bot.base import (
    ACTIVE,
    CHAIN_KINDS,
    HASHTAG,
    HELP,
    JOB_KINDS,
    JOB_LABELS,
    PAUSED_TEXT,
    RESUMED_TEXT,
    WORKER_DIED,
    JobError,
    hm,
    utcnow,
)
from reelline.pipeline.approve_bot.bot import ApproveBot
from reelline.pipeline.approve_bot.caption import CaptionMixin, parse_caption_edit
from reelline.pipeline.approve_bot.cli import main
from reelline.pipeline.approve_bot.jobs import (
    _running,
    active_job,
    add_job,
    dispatch_queued,
    get_job,
    kill_worker,
    overdue,
    process_alive,
    publish_job,
    reap_overdue,
    spawn_process,
    step_limit_min,
    update_job,
)
from reelline.pipeline.approve_bot.reporting import (
    failure_text,
    job_reel,
    notify_reaped,
    pick_line,
    reopen_review,
    report_failure,
    report_recovery,
)
from reelline.pipeline.approve_bot.status import _review_held, expires_at, slot_picking, slot_state, status_text
from reelline.pipeline.approve_bot.worker import (
    RENDER_WAIT_LOG_S,
    Steps,
    _continue,
    _produce,
    _publish_kind,
    _run_kind,
    _supersede,
    run_job,
)

__all__ = [
    "ACTIVE",
    "CHAIN_KINDS",
    "HASHTAG",
    "HELP",
    "JOB_KINDS",
    "JOB_LABELS",
    "PAUSED_TEXT",
    "RENDER_WAIT_LOG_S",
    "RESUMED_TEXT",
    "WORKER_DIED",
    "ApproveBot",
    "CaptionMixin",
    "JobError",
    "Steps",
    "_continue",
    "_produce",
    "_publish_kind",
    "_review_held",
    "_run_kind",
    "_running",
    "_supersede",
    "active_job",
    "add_job",
    "dispatch_queued",
    "expires_at",
    "failure_text",
    "get_job",
    "hm",
    "job_reel",
    "kill_worker",
    "main",
    "notify_reaped",
    "overdue",
    "parse_caption_edit",
    "pick_line",
    "process_alive",
    "publish_job",
    "reap_overdue",
    "reopen_review",
    "report_failure",
    "report_recovery",
    "run_job",
    "slot_picking",
    "slot_state",
    "spawn_process",
    "status_text",
    "step_limit_min",
    "update_job",
    "utcnow",
]
