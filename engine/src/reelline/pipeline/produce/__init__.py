"""Produce: a scripted pick -> rendered reel + cover -> assembled caption -> Telegram review.

produce() takes the slot's pick (status in steps.produce.pick_statuses: scripted, or
render_failed for a retry) and its latest script with a status in
steps.produce.script_statuses (ok or flagged), and renders it with scripts/render_reel.py
render() (reel and cover, named after the script file's stem, in out/reels/<name>/).
A failed render is retried once (steps.produce.render_attempts in all); failing again
stores a render_failed reel with the error, sets the pick to render_failed, sends a
Telegram alert and exits non-zero. After a render the post caption is assembled
(reelline/pipeline/caption.py assemble_caption(), with the line to the reel's credits page when
its images.json shows any image) and stored with the reel (the reels table: paths, duration, the
aligner's "check these" words, background, mood, TTS cost, render time, the cold open: gag, gag_s,
gag_mode); the pick
becomes rendered, and an earlier rendered reel of the same pick superseded.

Then QA (reelline/pipeline/qa.py run_flow(), unless --no-qa, which prints a loud notice and
says so in the review): a TTS re-roll of the paragraphs with low-score words, the
checks, one full re-render if they fail and the checks again. The final render's
paths, duration, check-these words and TTS cost (the re-roll's takes included) go
to the reel row with the QA status, failures and warnings; qa_<reel id>.json is in
the reel dir (one per reel: reels of the same script share the dir and its TTS
cache). A render that fails during QA makes the reel render_failed, with an alert.
Every paid call of the run (fresh TTS clips, QA's re-roll takes as tts_reroll,
image checks, generated illustrations, QA's frame checks; a failed attempt's too) is
written to llm_calls the moment it returns, with the script and the pick, and gets the
reel once it exists (reelline/pipeline/costs.py; a killed worker loses no spend).

Then the review goes to Telegram (unless --no-send): a header (reelline/review/machine.py
header_text()), the reel and cover
(scripts/send_to_phone.py, without its check-these list), the full caption, and a
status block, its last message with the review buttons (reelline/review/machine.py records
it; reelline/pipeline/approve_bot.py answers them): fact-check (✅, or ⚠️ with the failed claims), draft-lock warnings
(changed or dropped draft sentences), the end card (its lines; ⚠️ with any left off and why), QA (✅, or ❌ with the failures; ⚠️ warnings,
the low-score words among them, once; the re-roll and re-render), the cold open (🎬: the gag, or
none, and its A/B arm; no line with the cold open off: the straight style, reelline/media/style.py, or gags.json
mode off), background, mood,
duration and the reel's cost so far (its llm_calls as reelline/pipeline/costs.py counts them
for the reel, split into LLM, images, QA and TTS: the same total the cost report
gives the reel). No buttons: approval is the next step.

Backgrounds rotate over the drop-in folder (reelline/media/backgrounds.py): the enabled
backgrounds sorted by name, the next one after the last one a reel was rendered
with (the latest reel that rendered, whatever chose it or happened to it), wrapping
around; a last one that's gone or disabled continues from where its name would
sort. A manual --background is recorded like any other, so the next automatic
pick continues after it. Failed renders don't move the rotation.

Mood: choose_mood(), the style's for every reel for now (reelline/media/style.py: straight's happy), else
steps.produce.default_mood.

Layout: base, store (pick, script, reel rows), backgrounds, rendering, run (produce), qa, review, report
(status block), cli. The command line reaches produce() and Review through this package's namespace, so
tests can patch them here.
"""

from __future__ import annotations

from reelline.notify.base import split_text
from reelline.paths import REPO_ROOT
from reelline.pipeline.produce.backgrounds import (
    BackgroundChoice,
    backgrounds_report,
    choose_background,
    choose_mood,
    last_background,
    rotation_pick,
)
from reelline.pipeline.produce.base import Produced, ProduceError, RenderFailed, attempts_error, rel_path, short_error
from reelline.pipeline.produce.cli import USAGE, cmd_backgrounds, cmd_review, cmd_run, cmd_show, main
from reelline.pipeline.produce.qa import NO_QA_NOTICE, qa_part, run_qa, skip_qa
from reelline.pipeline.produce.rendering import RENDER_OPTIONS, Rendered, Renderer, RenderJob, render_reel
from reelline.pipeline.produce.report import cost_line, gag_lines, qa_lines, reel_report, status_block, version_lines
from reelline.pipeline.produce.review import Review, deliver_review, review_part, review_rendered, send_review_messages
from reelline.pipeline.produce.run import failed_alert, produce, render_part
from reelline.pipeline.produce.store import insert_reel, latest_script, slot_pick, supersede, update_reel

__all__ = [
    "NO_QA_NOTICE",
    "RENDER_OPTIONS",
    "REPO_ROOT",
    "USAGE",
    "BackgroundChoice",
    "ProduceError",
    "Produced",
    "RenderFailed",
    "RenderJob",
    "Rendered",
    "Renderer",
    "Review",
    "attempts_error",
    "backgrounds_report",
    "choose_background",
    "choose_mood",
    "cmd_backgrounds",
    "cmd_review",
    "cmd_run",
    "cmd_show",
    "cost_line",
    "deliver_review",
    "failed_alert",
    "gag_lines",
    "insert_reel",
    "last_background",
    "latest_script",
    "main",
    "produce",
    "qa_lines",
    "qa_part",
    "reel_report",
    "rel_path",
    "render_part",
    "render_reel",
    "review_part",
    "review_rendered",
    "rotation_pick",
    "run_qa",
    "send_review_messages",
    "short_error",
    "skip_qa",
    "slot_pick",
    "split_text",
    "status_block",
    "supersede",
    "update_reel",
    "version_lines",
]
