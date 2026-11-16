"""The English language pack: dates and the fact checks' stems for English text. Minimal: a channel writing in
English fills in what its checks need (fx_value_forms, notice_stems)."""

from __future__ import annotations

import re

from reelline.language.base import Language

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _is_latin(ch: str) -> bool:
    return "A" <= ch <= "Z" or "a" <= ch <= "z"


def _whole_words(n: int) -> int:
    """English says a number in about as many words as it has digit groups of 3, plus the scale words."""
    if n < 100:
        return 1 if n < 21 or n % 10 == 0 else 2
    hundreds, rest = divmod(n, 100) if n < 1000 else (0, 0)
    if n < 1000:
        return 2 + (_whole_words(rest) if rest else 0)  # "four hundred" + the rest
    for size in (10**12, 10**9, 10**6, 10**3):
        if n >= size:
            high, rest = divmod(n, size)
            return _whole_words(high) + 1 + (_whole_words(rest) if rest else 0)
    raise AssertionError((n, hundreds))


EN = Language(
    code="en",
    month_forms={i + 1: (name, name[:3], name[:3] + ".") for i, name in enumerate(MONTH_NAMES)},
    weekdays=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    year_word="",
    letter_class="A-Za-z",
    is_script_char=_is_latin,
    script_tail=re.compile(r"[A-Za-z]+$"),
    whole_words=_whole_words,
    month_stems=tuple(name.casefold()[:3] for name in MONTH_NAMES),
    money_stems=("dollar", "euro", "usd", "eur"),
    scale_stems=("thousand", "million"),
    free_stem="free",
    years_stem="year",
    places_stem="place",
    fx_month_names=MONTH_NAMES,
    spoken_latin_ok=True,
)
