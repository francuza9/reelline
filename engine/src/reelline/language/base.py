"""What a language pack provides: the words and forms the engine's text checks and date parsing need.
Data and small functions only; a pack for a new language fills these in (see ka.py, en.py)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Language:
    code: str
    # Dates: every written form of each month (1-12), the weekdays (Monday first), the word between a year and
    # a day ("2026 <word> 24 ..."), and a regex character class of the language's letters (a month is a whole word).
    month_forms: dict[int, tuple[str, ...]]
    weekdays: tuple[str, ...]
    year_word: str
    letter_class: str
    # Script: a character of the language's alphabet, a regex for an alphabet ending at the end of a word.
    is_script_char: Callable[[str], bool]
    script_tail: re.Pattern[str]
    # Spoken length of a whole number in words, of a time (19:00), and the extra words a decimal adds.
    whole_words: Callable[[int], int]
    time_words: int = 2
    decimal_word_count: int = 1
    # Key facts in a sentence (pipeline/script/facts.py): word stems after a number, stems of a "when you hear
    # back" clause, case endings of a place name, words that look like a place and aren't.
    month_stems: tuple[str, ...] = ()
    money_stems: tuple[str, ...] = ()
    scale_stems: tuple[str, ...] = ()
    place_endings: tuple[str, ...] = ("",)
    not_places: frozenset[str] = frozenset()
    notice_stems: tuple[str, ...] = ()
    free_stem: str = ""
    years_stem: str = ""
    places_stem: str = ""
    name_vowels: str = ""  # a name ending in one of these also matches without it
    until_ending: str = ""  # "20 <month>-until": the ending that makes a day a deadline
    # Relative day words banned in a script (whole words, word stems, two-word phrases), words a model writes for
    # a missing fact, and the letters of each Rhubarb mouth shape (lip-sync, media/visemes.py).
    relative_day_words: frozenset[str] = frozenset()
    relative_day_stems: tuple[str, ...] = ()
    relative_day_phrases: tuple[tuple[str, str], ...] = ()
    missing_fact_words: tuple[str, ...] = ()
    mouth_letters: dict[str, str] = field(default_factory=dict)
    # fx cards (media/fx): how each template's value is written (shown to the model), the month names a card shows
    # (nominative), and the format words and what they mean.
    fx_value_forms: dict[str, str] = field(default_factory=dict)
    fx_month_names: tuple[str, ...] = ()
    fx_formats: dict[str, str] = field(default_factory=dict)
    # A wider test of the alphabet for fx values and directive text (also historical letters); defaults to is_script_char.
    # Texts the checks show the model: what an fx unit / a city / a country looks like ({n}: the unit's longest
    # length), and a date written out ("write the date itself (9 October)").
    fx_unit_hint: str = "one word of at most {n} letters"
    fx_location_hint: str = "a city, nominative"
    fx_abroad_hint: str = "a country by its name in the nominative; no cities"
    date_example: str = "9 October"
    is_script_char_wide: Callable[[str], bool] | None = None
    # The TTS reads digits and Latin letters as they are (English): the spoken side of a script needs no {display|spoken} for them.
    spoken_latin_ok: bool = False
    # The language's letters can be aligned to the voice for lip sync and caption timing (scripts/align.py, the MMS
    # aligner: Georgian only so far); without it a reel can't be voiced and timed yet, only its cover rendered.
    lipsync_align: bool = False

    def script_char_wide(self, ch: str) -> bool:
        return (self.is_script_char_wide or self.is_script_char)(ch)
