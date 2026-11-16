"""The daily schedule: cron (a systemd timer on the server) runs `tick` every
steps.run_daily.tick_every_min; the tick decides everything from RUN_DAILY in
the channel config's steps.run_daily (Tbilisi time), starts what's due as detached workers and returns
in seconds. No times in the crontab.

Usage:
  python -m reelline.pipeline.run_daily tick
      What cron calls. Paused (/pause in the bot): only the read-only stats snapshots.
  python -m reelline.pipeline.run_daily slot HH:MM [--date YYYY-MM-DD]
      Start (or move on) one slot's chain now, whatever the time.
  python -m reelline.pipeline.run_daily status
      The bot's /status.
  python -m reelline.pipeline.run_daily pause | resume
      The bot's /pause and /resume (said in the chat too).
  python -m reelline.pipeline.run_daily evening [--print]
      Send the evening summary now (--print: only print it).
  python -m reelline.pipeline.run_daily install-cron
      Print the crontab lines (absolute paths, the venv, the node directory found now,
      .env, the daily log). Nothing is written.

A tick, in order:
  0. Hung jobs: a running job whose step ran past steps.run_daily.step_max_min is killed
     and fails as a dead worker (approve_bot.reap_overdue(); the bot does the same), with
     one alert, so its chain resumes below and it holds no other slot back.
  1. The bot: its heartbeat (bot_state, written every poll) older than
     steps.run_daily.bot_heartbeat_stale_min -> restarted detached (unless a bot process
     is still there, hung), with one alert. With steps.run_daily.bot_supervisor = systemd (the server's .env:
     REELLINE__STEPS__RUN_DAILY__BOT_SUPERVISOR=systemd)
     (the server: the bot's unit restarts it) only the alert, never a second bot.
  2. The evening delivery, from steps.run_daily.evening_delivery_at the day before an
     evening-batch slot: the evening summary once (today's posts and their links, the
     views line, today's spend, tomorrow's slot), then every held review (a rendered
     reel of tomorrow's slots that has none yet; day_slots()). Not paused: the summary
     waits for /resume, the reviews don't.
  3. Ingest every steps.run_daily.ingest_every_h (a detached task, daily_tasks).
  4. Each slot's chain, in slot order, one at a time (a slot whose chain has no job
     waits while another slot's chain job runs: the server's memory). It starts at
     chain_start(): for an evening-batch slot (steps.run_daily.evening_batch_from on)
     evening_start_at the day before, else slot - chain_start_before_h (the same-day
     chain), or the one-off steps.run_daily.test_override: ingest (skipped when one ran
     within ingest_skip_if_ran_min) -> a "pick" job (pick.py run: auto, queued, or a
     menu in a same-day chain) that goes on into the script and the reel
     (approve_bot.py, as the bot's script_produce) -> the review in Telegram (an evening
     chain's review is held until evening_delivery_at; produce.py asks review_held()).
     A menu pauses the chain; the bot resumes it (answer or timeout); the tick never
     answers a menu. Every tick picks the chain up from the pick's state, so a crash is
     never restarted from scratch. A worker whose process died is started again
     (steps.run_daily.max_resumes per chain); a step that failed with an error waits for its
     Retry button (the job's failure report). An evening chain with no review by the
     same-day start (slot - chain_start_before_h) falls back: it runs again from where
     it stopped (a new job after a failed or empty one), silently, and its review goes
     out when ready (chains.mode fallback). Not started by slot - catch_up_until_before_h:
     the slot is skipped and reported.
     A slot a reel holds (slot_reel(): approved for it, or gone out for it, early or
     not, Post now included) never gets another chain: its chain is done, and no pick,
     script or reel is made for it again, whatever pick, job or Retry is left there.
     A worker that died after the render, before the review (the pick rendered, its
     reel without one, not held for the evening): an approve_bot "qa_review" job runs QA
     on the render as it is and sends the review, once per reel; a chain that still
     closes without a review is reported. A slot still waiting for another slot's job
     steps.run_daily.wait_alert_after_min after its start is reported once.
  5. One alert per slot with no review by slot - alert_no_review_before_h: a slot has one
     when a review of its pick is pending or approved (or the pick is approved, posted or
     skipped, or a reel holds the slot).
  6. One alert per Claude call whose thinking took more than models.writer.thinking_alert_share
     of models.writer.max_tokens (thinking_alerts(); paused or not), the next ceiling.
  7. Queued jobs started (approve_bot.dispatch_queued(): the bot does the same; a job
     is claimed atomically, so it never runs twice).

The chains table is the lock: the tick advancing a chain holds it (pid + heartbeat);
another tick skips it; one whose process is gone is released and reported.
Approval and publishing stay the bot's (publish.enabled and the channel flags as
they are). Logs: stdout, which cron appends to logs/tick-<date>.log; the tick removes
logs older than steps.run_daily.log_keep_days. Credentials from .env only.
"""

from __future__ import annotations

from reelline.pipeline.run_daily.advance import (
    _add,
    _advance,
    _menu_open,
    advance,
    fall_back,
    fallback_due,
    review_out,
    unreviewed_reel,
)
from reelline.pipeline.run_daily.alerts import late_alerts, supervise_bot, thinking_alerts, waited_alert
from reelline.pipeline.run_daily.base import CHAIN_KINDS, DIED, hm, utcnow
from reelline.pipeline.run_daily.chains import acquire, get_chain, last_job, new_chain, release, set_chain, slot_reel
from reelline.pipeline.run_daily.cli import cron_lines, main
from reelline.pipeline.run_daily.effects import Effects, bot_pids, log_path, once, pid_alive, spawn_detached
from reelline.pipeline.run_daily.evening import deliver, evening_text, held_reel, posted_lines, slot_title
from reelline.pipeline.run_daily.ingest import ingest_due, ingest_state, ingest_worker, start_ingest
from reelline.pipeline.run_daily.slots import (
    batch_slot,
    candidate_slots,
    chain_mode,
    chain_start,
    claim_review,
    day_slots,
    delivery_at,
    evening_of,
    review_held,
    same_day_start,
    slot_dt,
)
from reelline.pipeline.run_daily.tick import (
    LIGHT_JOBS,
    one_at_a_time,
    prune_logs,
    retention_step,
    run_steps,
    stats_step,
    tick,
)

__all__ = [
    "CHAIN_KINDS",
    "DIED",
    "LIGHT_JOBS",
    "Effects",
    "_add",
    "_advance",
    "_menu_open",
    "acquire",
    "advance",
    "batch_slot",
    "bot_pids",
    "candidate_slots",
    "chain_mode",
    "chain_start",
    "claim_review",
    "cron_lines",
    "day_slots",
    "deliver",
    "delivery_at",
    "evening_of",
    "evening_text",
    "fall_back",
    "fallback_due",
    "get_chain",
    "held_reel",
    "hm",
    "ingest_due",
    "ingest_state",
    "ingest_worker",
    "last_job",
    "late_alerts",
    "log_path",
    "main",
    "new_chain",
    "once",
    "one_at_a_time",
    "pid_alive",
    "posted_lines",
    "prune_logs",
    "release",
    "retention_step",
    "review_held",
    "review_out",
    "run_steps",
    "same_day_start",
    "set_chain",
    "slot_dt",
    "slot_reel",
    "slot_title",
    "spawn_detached",
    "start_ingest",
    "stats_step",
    "supervise_bot",
    "thinking_alerts",
    "tick",
    "unreviewed_reel",
    "utcnow",
    "waited_alert",
]
