"""Script pass constants: prompt placeholders, empty-section fills, shared patterns, ScriptError and PassResult."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from reelline.core.errors import ReellineError
from reelline.media.script_markup import (
    Script,
)
from reelline.providers import LLMReply

# The prompts' {{NAME}} placeholders this code fills, each exactly once (the code's contract with
# the channel's prompt files, not settings), and the fills for an empty section.
STORY_PLACEHOLDERS = ("NEWS_BRIEF", "PUBLISH_DATE", "HOOK_EXAMPLES")
PUNCHUP_PLACEHOLDERS = (
    "NEWS_BRIEF",
    "DRAFT",
    "RECENT_LINES",
    "PREVIOUS_OPENING",
    "PUBLISH_DATE",
    "FLIRT",
    "RECENT_DETAILS",
    "RECENT_SHARES",
)
# No jokes to avoid repeating, no opening emotion, no flirt line.
STRAIGHT_PLACEHOLDERS = ("NEWS_BRIEF", "DRAFT", "PUBLISH_DATE", "RECENT_DETAILS", "RECENT_SHARES")
ENDCARD_PLACEHOLDERS = ("NEWS_BRIEF", "SCRIPT", "PUBLISH_DATE")
FLIRT_ON_PLACEHOLDERS = ("ANGLE", "RECENT_FLIRTS")
PROOFREAD_PLACEHOLDERS = ("KIND", "TEXT")
# {{RECENT_FLIRTS}} when there are no recent flirt lines.
NO_RECENT_FLIRTS = "(none yet)"
# {{RECENT_LINES}} when there are no recent videos to avoid repeating.
NO_RECENT_LINES = "(none yet)"
# {{RECENT_DETAILS}} when no recent script has a details line.
NO_RECENT_DETAILS = "(none yet)"
# {{RECENT_SHARES}} when no recent script has a share line.
NO_RECENT_SHARES = "(none yet)"
# {{PREVIOUS_OPENING}} when the previous script had no opening emotion (or there is none).
NO_PREVIOUS_OPENING = "(none)"

_TAG = re.compile(r"<[^<>]*>")
_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
_THOUSANDS = re.compile(r"(?<=\d)[ ,](?=\d{3}\b)")  # 20 000 and 20,000 are one number
_UNIT_SIGNS = "%₾$€"  # each said as a word of its own
_LETTER_FIRST = re.compile(r"\D*?[^\W\d_]")  # a letter before the token's first digit
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_FENCE = re.compile(r"^```[a-zA-Z]*\n(.*)\n```$", re.DOTALL)
_DASHES = ("—", "–")
# The story pass's openings block (prompts/story.md "Length and output"): after the story, the
# line OPENINGS: and a JSON object, {"used_score", "runners_up": [{"opening", "score"}, ...]}.
OPENINGS_MARKER = "OPENINGS:"
_OPENINGS_LINE = re.compile(r"^[ \t*#]*OPENINGS[ \t*]*:[ \t*]*", re.IGNORECASE)
OPENING_RUBRIC = 7  # the lines of the story prompt's opening rubric, a point each
ALT_OPENINGS = 2  # the runners-up kept
_EXAMPLES_LINE = re.compile(r"^[ \t]*\{\{HOOK_EXAMPLES\}\}[ \t]*\n(?:[ \t]*\n)?", re.MULTILINE)
# The story prompt's rubric line 6 (the host's loud opening) in the straight style, which has no opening
# emotion: (the classic words, the straight ones); the rest of prompts/story.md fits both.
STRAIGHT_OPENING = ("(outraged, disgusted or in mock excitement)", "(straight and upbeat, never sarcastic)")


class ScriptError(ReellineError, RuntimeError):
    """A pass that failed its checks twice, or a Claude call that failed."""


@dataclass
class PassResult:
    text: str
    replies: list[LLMReply] = field(default_factory=list)
    script: Script | None = None  # punch-up only
    retried: str | None = None  # the problem that caused the follow-up turn
    openings: dict | None = None  # story only: split_openings()'s, the runners-up or the problem
    fx_repaired: list[str] | None = None  # punch-up: the fact cards' repair in code, what it did and left


def _with_failures(prompt: str, failures: list[str] | None, what: str) -> str:
    if not failures:
        return prompt
    lines = "\n".join(f"- {f}" for f in failures)
    return (
        f"{prompt}\n\n# Fix these problems\nA fact-check of the previous {what} found statements the brief "
        f"doesn't support. Don't repeat them; stay strictly within the brief's facts:\n{lines}\n"
    )


def _today() -> date:
    from reelline.pipeline.scoring import tbilisi

    return datetime.now(UTC).astimezone(tbilisi()).date()


# ---- Story pass ------------------------------------------------------------------------
