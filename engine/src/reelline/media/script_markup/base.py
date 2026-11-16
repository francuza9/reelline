"""Markup constants and the error."""

from __future__ import annotations

import re

from reelline import language
from reelline.config import conf
from reelline.core.errors import ReellineError

PARAGRAPH_SEP = "\n\n"

GROUP = re.compile(r"\{([^{}|]*)\|([^{}|]*)\}")
TAG = re.compile(r"<([^<>]*)>")
BLANK_LINE = re.compile(r"\n[ \t]*\n")
DIRECTIVE = re.compile(r"@(\w+):(.*)")
DIRECTIVES = ("style", "card", "image", "fx")
COVER = "cover"
MORE_IMAGES = "image+"  # _directives(): the paragraph's extra @image values, one per line
FLIRT = "flirt"  # a bare "@flirt" line, no colon, no value
FLIRT_MAX_WORDS = 12  # the flirt line: one short sentence (prompts/flirt_on.md)
DETAILS = "details"  # a bare "@details" line: the line pointing to the end card
DETAILS_MAX_WORDS = 10  # the details line: one short sentence (prompts/punchup.md)
SHARE = "share"  # a bare "@share" line: "send this to your friend who..."
SHARE_MAX_WORDS = 20  # the share line: one or two short sentences (prompts/punchup.md)
SHARE_MAX_SENTENCES = 2
BARE = (FLIRT, DETAILS, SHARE)  # directives without a value
MARKUP_CHARS = "{}|"

# Every script ends with this paragraph (see the module docstring). Hyphens, never em dashes.
TAG_LINE = conf().steps.script.tag_line

# Gemini TTS vocal tags a script may use; anything else in <...> is an error.
# Only energetic ones: sighs, yawns and long pauses drag the delivery.
VOCAL_TAGS = frozenset({"chuckle", "snicker", "laugh", "snort", "tsk", "gasp", "short pause"})

# @style is a short nuance on top of the base voice, and must not pull it towards
# low energy. Banned words, with the forms of each that are also rejected; matching
# is on whole words (letters only, case-insensitive), so "slow-paced" is caught via
# "slow" but "software" is not caught via "soft".
STYLE_MAX_WORDS = 6
STYLE_BANNED = {
    "whisper": ("whisper", "whispers", "whispered", "whispering", "whispery"),
    "slow": ("slow", "slowly", "slower", "slowest", "slowed", "slowing"),
    "lazy": ("lazy", "lazily", "laziness"),
    "sleepy": ("sleepy", "sleepily", "sleepiness"),
    "tired": ("tired", "tiredly", "tiredness"),
    "calm": ("calm", "calmly", "calmer", "calmness"),
    "quiet": ("quiet", "quietly", "quieter", "quietness"),
    "soft": ("soft", "softly", "softer", "softest", "softness"),
    "sinking": ("sinking", "sink", "sinks", "sank", "sunk"),
    "yawn": ("yawn", "yawns", "yawned", "yawning", "yawny"),
    "mumble": ("mumble", "mumbles", "mumbled", "mumbling"),
}
_BANNED_FORMS = {form: word for word, forms in STYLE_BANNED.items() for form in forms}

# The opening emotion: paragraph 1's @style may be exactly one of these variants (case
# and spacing aside). Only paragraph 1's first sentence gets it (reelline/media/tts.py
# OPENING_DELIVERY: its style instead of the base one, a vocal tag first for "outrage"
# and "disgust tsk", "!" at the end); the rest of the reel is the base voice. Their
# words, and shouting, are reserved for it: no other @style may use OPENING_WORDS
# ("fake shock" is an ordinary nuance: prompts/punchup.md's safe default).
OPENING_EMOTIONS = ("outrage", "disgust", "disgust tsk")
OPENING_WORDS = frozenset(
    {
        "outrage",
        "outraged",
        "outrageous",
        "disgust",
        "disgusted",
        "disgusting",
        "disbelief",
        "disbelieving",
        "incredulous",
        "shout",
        "shouts",
        "shouting",
        "shouted",
        "scream",
        "screaming",
        "yell",
        "yelling",
    }
)
# Paragraph 1 is never smug (prompts/punchup.md): a paragraph-1 @style with one of these
# words is rejected (whole words, letters only, case-insensitive). Later paragraphs may be.
SMUG_FORMS = frozenset({"smug", "smugly", "smugness", "smugger", "smuggest"})
# Paragraph 1's style -> its opener animation on the first word (remotion/src/opening.ts
# OPENING.animations, the rig's moves): every allowed paragraph-1 style has one. Another
# style takes the first OPENER_KEYWORDS word it contains, else DEFAULT_OPENER_ANIMATION
# (also for no style): fake shock's. There is no smug opener.
OPENER_ANIMATIONS = {
    "outrage": "outrage",
    "disgust": "disgust",
    "disgust tsk": "disgust",
    "mock excitement": "excited",
    "fake shock": "shock",
}
OPENER_KEYWORDS = (("outrage", "outrage"), ("disgust", "disgust"), ("shock", "shock"), ("excite", "excited"))
DEFAULT_OPENER_ANIMATION = "shock"
# Relative day words, banned in a script (the reel is made the evening before it's
# published): whole words, or words starting with a stem (their case forms), and two-word
# phrases. The channel's language pack lists them. Matched on letters only.
_LANG = language.current()
RELATIVE_DAY_WORDS = _LANG.relative_day_words
RELATIVE_DAY_STEMS = _LANG.relative_day_stems
RELATIVE_DAY_PHRASES = _LANG.relative_day_phrases
# Where paragraph 1's first sentence ends: sentence punctuation, closing quotes, whitespace.
_FIRST_SENTENCE_END = re.compile(r"[.!?\u2026][\u201c\u201d\"\u00bb)]*\s+")

# @image: a search query is 1..IMAGE_MAX_WORDS ASCII words (letters, digits and
# ' & . - inside a word), with at least one letter; `lib:<id>` pins a library image
# (id = the first IMAGE_ID_LEN hex digits of the file's sha256, see the channel's images).
IMAGE_MAX_WORDS = 8
IMAGE_MAX_PER_PARAGRAPH = 3
# The word before an extra picture's anchor ends with one of these (a sentence or clause
# boundary), so the picture never changes mid-phrase.
_CLAUSE_END = ",.!?;:\u2026\u2014\u2013"
_CLAUSE_CLOSERS = "\"'\u201c\u201d\u201e\u00ab\u00bb)"
IMAGE_ID_LEN = 12
IMAGE_NONE = "none"
IMAGE_LIB_PREFIX = "lib:"
_IMAGE_WORD = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9'&.-]*[A-Za-z0-9.])?")
_IMAGE_ID = re.compile(rf"[0-9a-f]{{{IMAGE_ID_LEN}}}")

# @cover: 1..COVER_MAX_WORDS words (a word has a letter or digit; a lone "-" isn't
# one) of Georgian letters, digits, Latin (brand names) and COVER_PUNCT. Display text
# only: no {display|spoken} groups, no vocal tags.
COVER_MAX_WORDS = 6
COVER_PUNCT = frozenset(".,!?:;-'\"%&+/()„“”«»…")


class MarkupError(ReellineError, ValueError):
    """Raised for malformed markup or a spoken text TTS/the aligner can't take."""
