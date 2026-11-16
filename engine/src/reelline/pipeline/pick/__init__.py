"""Pick: turn ingested stories into one chosen story per posting slot.

Usage:
  python -m reelline.pipeline.pick assess [--force | --stale] [--grep PATTERN] [--story ID ...]
      Assess every stored story not assessed yet with Gemini (prompts/assess.md),
      steps.pick.assess_batch per call. --force redoes them (all, or the ones matching
      --grep / --story) and reports each story whose ban, topic, eligibility or base
      score changed. --stale redoes the stories from enabled sources fetched in the
      last steps.pick.reassess_days days whose assessment is from an older
      steps.pick.assess_prompt_version (they're out until then), for the next slot's
      publish date. --grep takes a regex over title and text, or a preset name
      from steps.pick.grep_presets ("government").
  python -m reelline.pipeline.pick list [--topic T] [--all]
      The ranking for the next slot: eligible stories with base and effective
      score and how the score was made; --all adds the ineligible ones with the
      gates they failed; --topic keeps one topic.
  python -m reelline.pipeline.pick queue
      Queued picks in order, each re-checked against the next slot.
  python -m reelline.pipeline.pick supply [--days N]
      The supply check (education only, assess prompt v4): every story first seen in
      the last N days (default 30) from a source enabled in sources.yaml is assessed
      again, dry (nothing stored but the calls' cost, tagged manual), as of its first
      slot (seen before steps.run_daily.evening_start_at: the next day's, else the day
      after), and judged by the gates and the score at every slot from then on, our own
      pick history left out (repeats, dismissals, posted). Prints the eligible stories
      per slot, the distinct ones per day and why the rest are out, and writes the
      details to out/supply/.
  python -m reelline.pipeline.pick brief <story_id>
      (Re)build the fact brief for a story (content/briefs/).
  python -m reelline.pipeline.pick run --slot HH:MM [--date YYYY-MM-DD] [--dry-run]
      Pick for one slot (channel.slots, Tbilisi time; the date defaults to today in
      Tbilisi). In order: assess new stories; use the first queued story that is
      still eligible (queued ones that aren't are rejected, with the reason); else
      rank the eligible stories (reelline/pipeline/scoring.py). Two or more close contenders
      (PICK contender_min / contender_within) go to a Telegram menu; otherwise the
      top one is picked. Nothing eligible: a Telegram alert with the most common
      reasons, no pick, nothing off-topic (exit 1).
      --dry-run shows all of it without assessing, sending or writing.
  python -m reelline.pipeline.pick choose <menu_id> <n> [<n> ...]
      Answer a menu from the command line (the fallback when the bot isn't
      running): the first n goes to the menu's slot, the rest are queued for the
      next slots in that order, the unchosen ones are dismissed.
  python -m reelline.pipeline.pick none <menu_id>
      "None of these" from the command line: every contender dismissed, then the
      slot picked outside the menu (as `run`, never a second menu).
  python -m reelline.pipeline.pick dismiss-unselected <menu_id>
      Backfill for a menu answered before dismissals existed: its unchosen
      contenders still "new" are dismissed (menu_unselected, at the menu's answer
      time). The chosen ones, and anything else, are left as they are.

Menus are answered in Telegram through the approval bot (python -m
reelline.pipeline.approve_bot), the one listener for the review chat: it feeds the menu's
button taps to Picker.handle_update(), resolves menus past steps.pick.menu_timeout_min
with their #1 (expire(), said in the menu and the chat), and continues with the
script and the reel in a worker job.

After every pick (auto, queued, menu or timeout) the fact brief is built
(reelline/pipeline/brief.py) into content/briefs/.

A menu's toggles and Confirm work the same way: the first chosen story is picked for
the slot, the others are queued (story status "queued") and used before any new
ranking while they stay eligible. Every contender not chosen is dismissed for good
(story status "dismissed", a dismissals row: menu_unselected): never offered again,
by a ranking, a menu or the queue, and neither is the same story from another outlet
(scoring.dismissed_twin). "None of these" dismisses every contender (menu_none) and
the slot falls back to the best eligible story outside the menu (no second menu);
nothing eligible is the usual Telegram alert. A timeout dismisses nothing: #1 is
picked, the others stay eligible. Every decision is a row in picks (reelline/pipeline/db.py)
with the candidate snapshot; picked and queued stories leave the candidate pool.

Gemini: the key from .env (GEMINI_API_KEY) via reelline/providers/google.py; Telegram:
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID. Model, prompt versions and every number in
steps.pick (the channel config).

Layout: base (error, Ranking), loading (database reads), state/menus/picker (the Picker, in layers),
assessments, listing, supply and cli (the commands).
"""

from __future__ import annotations

from reelline.pipeline.pick.assessments import assessment_state, cmd_assess, report_changes, select_stories
from reelline.pipeline.pick.base import (
    PickError,
    Ranking,
    assess_cost,
    iso_ts,
    nothing_eligible_text,
    opt_bool,
    source_names,
    today_tbilisi,
)
from reelline.pipeline.pick.cli import (
    build_parser,
    cmd_brief,
    cmd_choose,
    cmd_dismiss_unselected,
    cmd_none,
    cmd_run,
    main,
)
from reelline.pipeline.pick.listing import cmd_list, cmd_queue, next_slot
from reelline.pipeline.pick.loading import load_candidates, load_dismissed, load_history
from reelline.pipeline.pick.picker import Picker
from reelline.pipeline.pick.supply import cmd_supply, supply, supply_text

__all__ = [
    "PickError",
    "Picker",
    "Ranking",
    "assess_cost",
    "assessment_state",
    "build_parser",
    "cmd_assess",
    "cmd_brief",
    "cmd_choose",
    "cmd_dismiss_unselected",
    "cmd_list",
    "cmd_none",
    "cmd_queue",
    "cmd_run",
    "cmd_supply",
    "iso_ts",
    "load_candidates",
    "load_dismissed",
    "load_history",
    "main",
    "next_slot",
    "nothing_eligible_text",
    "opt_bool",
    "report_changes",
    "select_stories",
    "source_names",
    "supply",
    "supply_text",
    "today_tbilisi",
]
