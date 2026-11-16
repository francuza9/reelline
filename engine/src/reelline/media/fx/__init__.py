"""Fact graphics (fx) in a reel: the @fx directive's values, when each card is out, and
where it sits (channel docs/fx.md "In a reel").

A paragraph's `@fx: <template> <value> [| <anchor word>]` line (reelline/media/script_markup.py)
names one of TEMPLATES and the fact it shows, written the way the template takes it
(parse_value(); VALUE_FORMS lists them). The anchor, a display word of the paragraph
(punctuation at its ends ignored), is where the card starts; without one, the paragraph's
first word. parse_value() is strict: a value a template can't show raises FxError, which
the parser turns into a MarkupError, so a bad one fails before TTS.

The tunables are remotion/src/fx.json's (the Remotion side draws the cards): `reel` for the
timing, `placements.popout` for the card and its safe area, each template's durationS.
Abroad's countries are remotion/src/fx/destinations.json's (names and aliases, nominative).

planned() and placed() place the cards (render_reel.py): from the anchor word's start for
leadS + the template's durationS + holdS + the flight back, back in the TV by the next
paragraph's first word (a too-short paragraph: minHoldS into the next one, only when that
one has no joke), cut short before the next card (gapS) and the end card (outroGapS); one
that would hold less than minHoldS is left out. Paragraph 1's card is the frame-0 card
(fx.json `opening`, the card's and event's frame0): out and settled from frame 0, whatever its
anchor, for at least opening.minHoldS or as long as a card on its first word would be,
whichever is later; a card after it that's too close is left out, never it.
caption_windows() are the stretches the captions stay on one line (the card reaches below
a two-line caption's top). days_left() is the deadline's tag, counted from the publish
(slot) date, never the render date; none when it would be 0 or less.

card_bounds(), caption_top() and eye_tops() are the settled card's box, a caption block's
top and the mascot's eyes in output pixels, for QA (reelline/pipeline/qa.py); Remotion checks the same at
render time (remotion/src/Fx.tsx checkPopoutBox).
"""

from __future__ import annotations

from reelline.media.fx.base import (
    ANCHOR_SEP,
    DAYS_LEFT_MAX,
    DESTINATIONS_JSON,
    DOMAIN,
    FORMATS,
    FRAME_H,
    FRAME_W,
    FX_JSON,
    FX_SUFFIX,
    GEORGIA_JSON,
    MAX_NUMBER,
    MAX_TEXT_CHARS,
    MAX_UNIT_CHARS,
    MONTH_DAYS,
    MONTHS,
    NOT_ON_CARD,
    REMOTION_SRC,
    STEP_MAX_CHARS,
    STEP_MAX_WORDS,
    TEMPLATES,
    UNIT_SIGNS,
    VALUE_FORMS,
    FxError,
)
from reelline.media.fx.geometry import _ts_number, caption_top, card_bounds, eye_tops, safe_problems
from reelline.media.fx.spec import cities, config, countries
from reelline.media.fx.timing import (
    back_s,
    caption_windows,
    days_left,
    deadline_date,
    frame0_on,
    placed,
    planned,
    publish_date_of,
    settle_s,
)
from reelline.media.fx.values import FxDirective, fx_path, numbers_in, parse_directive, parse_value, show, write_checked

__all__ = [
    "ANCHOR_SEP",
    "DAYS_LEFT_MAX",
    "DESTINATIONS_JSON",
    "DOMAIN",
    "FORMATS",
    "FRAME_H",
    "FRAME_W",
    "FX_JSON",
    "FX_SUFFIX",
    "GEORGIA_JSON",
    "MAX_NUMBER",
    "MAX_TEXT_CHARS",
    "MAX_UNIT_CHARS",
    "MONTHS",
    "MONTH_DAYS",
    "NOT_ON_CARD",
    "REMOTION_SRC",
    "STEP_MAX_CHARS",
    "STEP_MAX_WORDS",
    "TEMPLATES",
    "UNIT_SIGNS",
    "VALUE_FORMS",
    "FxDirective",
    "FxError",
    "_ts_number",
    "back_s",
    "caption_top",
    "caption_windows",
    "card_bounds",
    "cities",
    "config",
    "countries",
    "days_left",
    "deadline_date",
    "eye_tops",
    "frame0_on",
    "fx_path",
    "numbers_in",
    "parse_directive",
    "parse_value",
    "placed",
    "planned",
    "publish_date_of",
    "safe_problems",
    "settle_s",
    "show",
    "write_checked",
]
