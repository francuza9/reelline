"""Shared by every db submodule: status vocabularies, DbError, Migration."""

from __future__ import annotations

from dataclasses import dataclass

from reelline.core.errors import ReellineError

# Story statuses. Checked here rather than with a CHECK constraint: SQLite can't
# alter a CHECK without rebuilding the table, and later steps may add statuses.
# dismissed: turned down in a pick menu (a dismissals row says how); never eligible again.
# archived: from a source with assess: false (reelline/pipeline/sources.py, the tech news archive):
# stored only, never assessed, never sent to a model, never picked.
STORY_STATUSES = ("new", "queued", "picked", "rejected", "used", "dismissed", "archived")
# Scripts: running (being written; left there by a crash), failed (no usable script), flagged (fact-check problems left; still goes to
# review with a warning), ok, then approved / posted (later steps).
SCRIPT_STATUSES = ("running", "failed", "flagged", "ok", "approved", "posted")
# skipped: the reviewer skipped the slot (reelline/pipeline/approve_bot.py); the story may stay eligible.
PICK_STATUSES = (
    "queued",
    "picked",
    "scripted",
    "rendered",
    "render_failed",
    "approved",
    "rejected",
    "posted",
    "skipped",
)
# Reels (reelline/pipeline/produce.py, reelline/pipeline/approve_bot.py): rendered (reel and cover made,
# review sent), render_failed (both attempts failed; error stored), superseded (a newer
# reel of the same pick, or its pick was dropped), approved, rejected, expired (no
# action by approve_bot.expires_at()), published (live on every enabled channel),
# partially_published (some channels failed; reelline/pipeline/publish.py), deleted (every
# channel's post taken down after it went live).
REEL_STATUSES = (
    "rendered",
    "render_failed",
    "superseded",
    "approved",
    "rejected",
    "expired",
    "published",
    "partially_published",
    "deleted",
)
# Publications (reelline/pipeline/publish.py), one per reel and channel: waiting (approved, the
# post not created yet), scheduling (being created), scheduled (on the platform),
# published, failed, unscheduled, deleted (taken down after going live, through the
# API or by hand).
PUBLICATION_STATES = ("waiting", "scheduling", "scheduled", "published", "failed", "unscheduled", "deleted")
# Reviews (reelline/review/machine.py): pending (buttons live; `state` says which), approved,
# rejected, superseded, expired.
REVIEW_STATUSES = ("pending", "approved", "rejected", "superseded", "expired")
# Worker jobs (reelline/pipeline/approve_bot.py): queued, running, done, failed.
JOB_STATUSES = ("queued", "running", "done", "failed")
# Chains (reelline/pipeline/run_daily.py): running (moving through its steps), waiting (a menu
# is open, the bot resumes it), done (the review is out, or the slot was decided),
# skipped (too late, or nothing eligible), failed (a step failed: its Retry button).
CHAIN_STATUSES = ("running", "waiting", "done", "skipped", "failed")
CHAIN_MODES = (None, "evening", "fallback")
CHOSEN_BY = ("auto", "human", "timeout")
# Menus (reelline/pipeline/pick.py): open, resolved (Confirm), timeout (no answer: #1 picked),
# none ("None of these": every contender dismissed, the slot picked outside the menu).
MENU_STATUSES = ("open", "resolved", "timeout", "none")
# Why a story was dismissed: menu_unselected (left out of a Confirm), menu_none (None of these).
DISMISS_REASONS = ("menu_unselected", "menu_none")
RUN_STATUSES = ("running", "ok", "errors", "crashed")


class DbError(ReellineError, RuntimeError):
    """Raised when the database is unusable or a write breaks the pipeline's rules."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]
    # A table rebuild (SQLite can't drop NOT NULL in place): foreign keys off around
    # the migration's transaction, checked with foreign_key_check before it commits
    # (SQLite's documented procedure for schema changes).
    rebuild: bool = False
