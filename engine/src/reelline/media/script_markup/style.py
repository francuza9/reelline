"""@style rules: banned words, the opening emotion and its animation, relative-day words."""

from __future__ import annotations

import re

from reelline.media.script_markup.base import (
    _BANNED_FORMS,
    _FIRST_SENTENCE_END,
    DEFAULT_OPENER_ANIMATION,
    OPENER_ANIMATIONS,
    OPENER_KEYWORDS,
    OPENING_EMOTIONS,
    OPENING_WORDS,
    RELATIVE_DAY_PHRASES,
    RELATIVE_DAY_STEMS,
    RELATIVE_DAY_WORDS,
    SMUG_FORMS,
    STYLE_MAX_WORDS,
    MarkupError,
)
from reelline.media.script_markup.model import Script


def opener_animation(script: Script) -> str:
    """The opener animation for the script's paragraph-1 style (OPENER_ANIMATIONS); a style that
    names none: the style preset's opener (reelline/media/style.py: straight's excited, never fake shock),
    else DEFAULT_OPENER_ANIMATION."""
    from reelline.media import style as reel_style

    p1 = script.paragraphs[0]
    style = " ".join((script.opening or p1.style or "").lower().split())
    if style in OPENER_ANIMATIONS:
        return OPENER_ANIMATIONS[style]
    default = reel_style.current().opener or DEFAULT_OPENER_ANIMATION
    return next((anim for word, anim in OPENER_KEYWORDS if word in style), default)


def relative_day_words(text: str) -> list[str]:
    """The relative day words in `text` (see RELATIVE_DAY_WORDS), in order."""
    words = [re.sub(r"[^\w]", "", w).lower() for w in text.split()]
    found = [w for w in words if w in RELATIVE_DAY_WORDS or w.startswith(RELATIVE_DAY_STEMS)]
    for first, stem in RELATIVE_DAY_PHRASES:
        found += [f"{a} {b}" for a, b in zip(words, words[1:], strict=False) if a == first and b.startswith(stem)]
    return found


def opening_emotion(style: str) -> str | None:
    """The OPENING_EMOTIONS entry an @style value is (case and spacing aside), or None."""
    value = " ".join(style.lower().split())
    return value if value in OPENING_EMOTIONS else None


def split_first_sentence(text: str) -> tuple[str, str | None]:
    """(the first sentence, the rest or None) of a paragraph's TTS text: split after the
    first sentence punctuation (and closing quotes) followed by whitespace."""
    m = _FIRST_SENTENCE_END.search(text)
    if m is None or not text[m.end() :].strip():
        return text.strip(), None
    return text[: m.end()].strip(), text[m.end() :].strip()


def _check_style(style: str, n: int) -> None:
    reserved = sorted({w for w in re.findall(r"[a-z]+", style.lower()) if w in OPENING_WORDS})
    if reserved:
        where = (
            "on paragraph 1 it is exactly one opening emotion: " + ", ".join(OPENING_EMOTIONS)
            if n == 1
            else "they're for paragraph 1's opening emotion only"
        )
        raise MarkupError(f"Paragraph {n}: @style {style!r} uses {', '.join(reserved)}; {where}")
    words = style.split()
    if len(words) > STYLE_MAX_WORDS:
        raise MarkupError(f"Paragraph {n}: @style has {len(words)} words, max {STYLE_MAX_WORDS}: {style!r}")
    banned = [w for w in re.findall(r"[a-z]+", style.lower()) if w in _BANNED_FORMS]
    if banned:
        raise MarkupError(f"Paragraph {n}: @style {style!r} uses low-energy word(s): {', '.join(banned)}")


def _check_not_smug(style: str) -> None:
    from reelline.media import style as reel_style

    smug = sorted({w for w in re.findall(r"[a-z]+", style.lower()) if w in SMUG_FORMS})
    if smug:
        # The fix the retry turn suggests is the style's (reelline/media/style.py): straight has no opening emotion.
        instead = (
            f"mock excitement, fake shock, or an opening emotion ({', '.join(OPENING_EMOTIONS)})"
            if reel_style.current().opening_emotion
            else "an energetic nuance (energetic, excited, upbeat) or none"
        )
        raise MarkupError(
            f"Paragraph 1: @style {style!r} is smug ({', '.join(smug)}); paragraph 1 is never smug: {instead}"
        )
