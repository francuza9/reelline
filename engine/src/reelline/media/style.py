"""The reels' style: one switch, STYLE_MODE, for the whole chain (channel docs/scripts.md "Straight mode").

classic: the cold open (remotion/src/gags.json's mode),
punch-up's 2-3 jokes (prompts/punchup.md, fed the recent jokes and the previous opening emotion), the
flirt line on FLIRT_PROBABILITY of the picks, the opening emotion, 2-4 fact cards, the closing jab's
push-in, eye-roll and bounce, the host resting smug.

straight (no jokes, no flirting, more straightforward, more visual cues):
  - no cold open (reelline/media/gags.py decide(), whatever gags.json's mode);
  - prompts/straight.md instead of punch-up: the same non-joke jobs (the pictures, the fact cards, the
    details line, the share line, paragraph splits, the tag, the format) and no jokes, no sarcasm, no
    sentence of its own (reelline/pipeline/script.py straight_problems()); no {{RECENT_LINES}} or
    {{PREVIOUS_OPENING}}, and the story prompt gets no {{HOOK_EXAMPLES}};
  - no flirt line (probability 0, a pick's stored roll ignored);
  - no opening emotion (paragraph 1 is voiced in the base voice), no smug or mocking @style, no laugh
    or tsk tags; paragraph 1's opener animation "excited" when its style names none;
  - a fact card on every deadline, age, amount, free and place the script says (fx_problems()), with
    the cards' spacing loosened (FX_REEL over remotion/src/fx.json's reel);
  - no closing-jab push-in, eye-roll or bounce, no bounce on the share line, which isn't a joke
    (MOTION over remotion/src/motion.json); the host rests happy (MOOD), not smug.

Switching back is the one line below: STYLE_MODE = "classic". Nothing else changes: the prompts, the
assets and gags.json stay as they are. Tests pin "classic" (tests/conftest.py) unless they test a
style; tests/test_straight.py tests "straight".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError

STYLE_MODE = "straight"


@dataclass(frozen=True)
class Style:
    name: str
    gags: bool  # the cold open as gags.json's mode says; False: none
    punchup_prompt: Path  # the second pass's prompt
    jokes: bool  # punch-up writes jokes: {{RECENT_LINES}}, {{PREVIOUS_OPENING}}, laugh tags
    flirt_probability: float | None  # the share of picks with a flirt line (None: FLIRT_PROBABILITY;
    # 0: none, a pick's stored yes overridden: reelline/pipeline/script.py pick_flirt())
    opening_emotion: bool  # paragraph 1 may take an opening emotion (outrage, disgust, disgust tsk)
    hook_examples: bool  # the story prompt's {{HOOK_EXAMPLES}} (data/hook_examples.json)
    every_fact_fx: bool  # every deadline, age, amount, free and place has its card (fx_problems())
    opener: str | None = None  # paragraph 1's opener animation when its style names none (None: the
    # default, reelline/media/script_markup.py DEFAULT_OPENER_ANIMATION)
    mood: str | None = None  # the reel's mood (The host's rest mouth) unless --mood (None: render_reel's)
    banned_styles: frozenset[str] = frozenset()  # @style words this style rejects (whole words)
    banned_tags: frozenset[str] = frozenset()  # vocal tags this style rejects
    fx_reel: dict = field(default_factory=dict)  # over remotion/src/fx.json "reel"
    motion: dict = field(default_factory=dict)  # over remotion/src/motion.json, per section


STYLES = {
    "classic": Style(
        name="classic",
        gags=True,
        punchup_prompt=conf().steps.script.punchup_prompt,
        jokes=True,
        flirt_probability=None,
        opening_emotion=True,
        hook_examples=True,
        every_fact_fx=False,
    ),
    "straight": Style(
        name="straight",
        gags=False,
        punchup_prompt=conf().steps.script.straight_prompt,
        jokes=False,
        flirt_probability=0.0,
        opening_emotion=False,
        hook_examples=False,
        every_fact_fx=True,
        opener="excited",
        mood="happy",
        # Sarcasm and contempt in a delivery note (prompts/straight.md rule 1 names each).
        banned_styles=frozenset(
            {
                "smug",
                "smugly",
                "mock",
                "mocking",
                "mockingly",
                "sarcastic",
                "sarcastically",
                "sarcasm",
                "ironic",
                "ironically",
                "irony",
                "deadpan",
                "teasing",
                "tease",
                "brutal",
                "brutally",
                "condescending",
                "contempt",
                "contemptuous",
                "fake",
                "snarky",
                "snide",
                "scornful",
                "dismissive",
                "bitchy",
                "cocky",
                "arrogant",
                "gloating",
                "roast",
                "roasting",
                "dry",
                "dryly",
                "cutting",
                "savage",
            }
        ),
        # The punchlines' tags (motion.json punchline.tags) and the disgusted tsk.
        banned_tags=frozenset({"chuckle", "snicker", "laugh", "snort", "tsk"}),
        # At least 3 cards, up to one per key fact (the story keeps 2-3 numbers, plus free, a place and
        # paragraph 1's stake), cards on paragraphs in a row and on the one before the tag (there's no
        # closing jab). A card too close to the one before it waits for it (queue) rather than being
        # left out, up to 1.5 s past its own paragraph's end: one-sentence fact paragraphs in a row are
        # ~1-3 s each, a card ~2.5-3 s. 0.3 s between two (classic 1.0; each still holds minHoldS
        # settled, then flies back).
        fx_reel={
            "minPerScript": 3,
            "maxPerReel": 7,
            "gapS": 0.3,
            "consecutive": True,
            "beforeTag": True,
            "queue": True,
            "queueLagS": 1.5,
        },
        motion={"punchline": {"closingJab": False}, "bounce": {"jab": False, "share": False}},
    ),
}


class StyleError(ReellineError, ValueError):
    """STYLE_MODE names no style."""


def current() -> Style:
    """The style STYLE_MODE names."""
    try:
        return STYLES[STYLE_MODE]
    except KeyError:
        raise StyleError(
            f"reelline/media/style.py: STYLE_MODE must be one of {', '.join(STYLES)}, got {STYLE_MODE!r}"
        ) from None


def over(spec: dict, overrides: dict) -> dict:
    """A deep copy of `spec` with `overrides` laid over it, dict by dict (a key `spec` lacks is added)."""
    out = copy.deepcopy(spec)
    for key, value in overrides.items():
        out[key] = over(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out
