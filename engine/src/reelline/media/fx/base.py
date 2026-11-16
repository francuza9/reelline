"""Paths, constants and the error shared by the fx submodules."""

from __future__ import annotations

import re

from reelline import language
from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.paths import REMOTION_DIR, graphics_file

REMOTION_SRC = REMOTION_DIR / "src"
# The graphics' data is the channel's (channel.graphics_dir), or the engine's default for a file it lacks;
# Remotion reads the same files as @channel/graphics/<name> (remotion.config.ts).
GRAPHICS_DIR = conf().channel.graphics_dir
FX_JSON = graphics_file(GRAPHICS_DIR, "fx.json")
DESTINATIONS_JSON = graphics_file(GRAPHICS_DIR, "destinations.json")
GEORGIA_JSON = graphics_file(GRAPHICS_DIR, "georgia.json")
FRAME_W, FRAME_H = 1080, 1920
# The fact-check's file next to the script (<name>.txt -> <name>.fx.json): the @fx it
# supported, which the render may show (reelline/pipeline/fx.py writes it).
FX_SUFFIX = ".fx.json"

TEMPLATES = (
    "deadline",
    "age",
    "money",
    "places",
    "condition",
    "free",
    "location",
    "abroad",
    "duration",
    "start",
    "certificate",
    "prize",
    "apply",
    "format",
    "steps",
)
# How each template's value is written after its name, and the month names and format words (language pack).
VALUE_FORMS = language.current().fx_value_forms
MONTHS = language.current().fx_month_names
MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
FORMATS = language.current().fx_formats
UNIT_SIGNS = ("₾", "$", "€")
MAX_NUMBER = 9_999_999
MAX_UNIT_CHARS = 12
MAX_TEXT_CHARS = {"prize": 16, "certificate": 14, "location": 20}
STEP_MAX_WORDS = 2
STEP_MAX_CHARS = 18
# The days-left tag shows at most two digits.
DAYS_LEFT_MAX = 99
ANCHOR_SEP = "|"
# Never on a card: script markup and dashes other than a hyphen (as on the end card).
NOT_ON_CARD = "{}|<>@—–"
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}")


class FxError(ReellineError, ValueError):
    """A value a template can't show, or a bad fx.json."""


def _georgian(ch: str) -> bool:
    return language.current().script_char_wide(ch)


def _georgian_word(word: str) -> bool:
    return bool(word) and all(_georgian(ch) for ch in word)


def config() -> dict:
    """fx.config(), looked up at call time (tests patch it on the package)."""
    from reelline.media import fx

    return fx.config()
