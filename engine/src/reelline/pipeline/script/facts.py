"""Key facts (deadline, age, money, places, free, place) found in a sentence, and which of them lack a card."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from reelline import language
from reelline.media.script_markup import (
    Script,
)
from reelline.pipeline.script.lock import sentences

# The key facts every one of which gets a card in the straight style (reelline/media/style.py every_fact_fx),
# found in a sentence's display words (key_facts()), and the templates that show each.
FACT_CARDS = {
    "deadline": ("deadline", "start"),  # a day and a month: "20 <month>-until"
    "age": ("age", "condition", "duration"),  # a number before a years word
    "money": ("money", "prize"),  # an amount: 500 <currency>, 5 <thousand> <currency>, 500₾
    "places": ("places", "prize"),  # a number before a places word
    "free": ("free",),  # the free word in any form
    "place": ("location", "abroad"),  # a city in Georgia or a country (reelline/media/fx.py's lists)
}
FACT_NAMES = {
    "deadline": "the deadline",
    "age": "an age",
    "money": "an amount of money",
    "places": "places",
    "free": "free",
    "place": "a place",
}
# Month stems, money units, scale words, a place name's case endings and the "when you hear back" stems:
# the channel's language pack (reelline/language).
_LANG = language.current()
_MONTH_STEMS = _LANG.month_stems
_MONEY_STEMS = _LANG.money_stems
_SCALE_STEMS = _LANG.scale_stems
_PLACE_ENDINGS = _LANG.place_endings
_NOT_PLACES = _LANG.not_places
_NOTICE_STEMS = _LANG.notice_stems
_CLAUSE_PUNCT = ",;:—–"


def _bare(word: str) -> str:
    return word.strip(".,!?:;„“\"'()…").casefold()


@lru_cache(maxsize=1)
def _place_forms() -> dict[str, str]:
    """Each form of a city's or a country's name (the name, and its stem without the final vowel,
    with a case ending) -> the name."""
    from reelline.media.fx import cities, countries

    forms: dict[str, str] = {}
    for name in [*cities(), *countries()]:
        name = name.casefold()
        if " " in name or name in _NOT_PLACES:
            continue  # a two-word name: the prompt asks for it, the check doesn't
        stems = {name, name[:-1]} if name[-1] in _LANG.name_vowels else {name}
        for stem in stems:
            for ending in _PLACE_ENDINGS:
                forms.setdefault(stem + ending, name)
    return forms


def key_facts(sentence: str) -> list[tuple[str, str]]:
    """(kind, the word that says it) per key fact in a display sentence, kinds from FACT_CARDS: a
    number before a month (the deadline; not in a clause that says when you hear back,
    _NOTICE_STEMS), before a year word (an age, never a 4-digit year), before a money unit
    (thousands words between) or a places word; a word with ₾ $ €; the free word; a city's or country's name
    in a case form."""
    raw = sentence.split()
    words = [_bare(w) for w in raw]
    clause, n = [], 0  # each word's clause: a , ; : or dash ends one
    for w in raw:
        clause.append(n)
        n += w.rstrip("\"'“”»)").endswith(tuple(_CLAUSE_PUNCT)) or w in ("—", "–")
    notice = {clause[i] for i, w in enumerate(words) if any(stem in w for stem in _NOTICE_STEMS)}
    places = _place_forms()
    found: list[tuple[str, str]] = []
    for i, word in enumerate(words):
        if not word:
            continue
        if _LANG.free_stem and word.startswith(_LANG.free_stem):
            found.append(("free", word))
        elif any(sign in word for sign in "₾$€") and any(ch.isdigit() for ch in word):
            found.append(("money", word))
        elif word in places or word.split("-")[0] in places:  # a place with a case ending after a hyphen
            found.append(("place", word))
        if not any(ch.isdigit() for ch in word):
            continue
        k = i + 1
        while k < len(words) and words[k].startswith(_SCALE_STEMS):
            k += 1
        nxt = words[k] if k < len(words) else ""
        digits = re.findall(r"\d+", word)
        said = " ".join(words[i : k + 1])
        if nxt.startswith(_MONTH_STEMS) and 1 <= int(digits[0]) <= 31:
            if clause[i] not in notice:
                found.append(("deadline", said))
        elif _LANG.years_stem and nxt.startswith(_LANG.years_stem) and int(digits[-1]) < 120:
            found.append(("age", said))
        elif nxt.startswith(_MONEY_STEMS):
            found.append(("money", said))
        elif _LANG.places_stem and nxt.startswith(_LANG.places_stem):
            found.append(("places", said))
    return found


def fact_key(kind: str, word: str) -> tuple:
    """One key fact however it's inflected: "27 <month, case 1>" and "27 <month, case 2>" are the same day, "500
    <currency>" and "500 <currency, case 2>" the same amount, a city in two cases the same city."""
    if kind == "free":
        return (kind,)
    if kind == "place":
        forms = _place_forms()
        return (kind, forms.get(word, forms.get(word.split("-")[0], word)))
    digits = tuple(re.findall(r"\d+", word))
    if kind == "deadline":
        month = next((m for m in _MONTH_STEMS if m in word), "")
        return (kind, digits[:1], month)
    return (kind, digits)


def uncarded_facts(script: Script) -> list[str]:
    """The straight style's rule (prompts/straight.md "Fact graphics"): every sentence that says a
    key fact (key_facts()) is in a paragraph whose card shows it, or another key fact of that
    sentence. A paragraph has one card, so two facts in two sentences need the paragraph split. The
    details, share and flirt lines and the tag aren't looked at (no card goes there). A fact an
    earlier sentence already said (fact_key(): the same day, amount, age or place in another case
    form) is a reminder, not a new fact, and needs no card of its own. [] if none."""
    found = []
    for u in uncarded(script):
        p = script.paragraphs[u.paragraph]
        said = ", ".join(f"{FACT_NAMES[kind]} „{word}“" for kind, word in u.facts)
        if p.fx is None:
            found.append(
                f"paragraph {u.paragraph + 1} says {said} but has no @fx: every deadline, age, amount, free "
                "and place gets its card, on the paragraph that says it"
            )
        elif len(sentences(p.display)) == 1:
            cards = " or ".join(dict.fromkeys(t for kind, _ in u.facts for t in FACT_CARDS[kind]))
            found.append(
                f"paragraph {u.paragraph + 1}'s card shows {p.fx.template}, but its only sentence "
                f"„{u.sentence}“ says {said}: make its @fx {cards}"
            )
        else:
            found.append(
                f"paragraph {u.paragraph + 1}'s card shows {p.fx.template}, but its sentence „{u.sentence}“ "
                f"says {said}: split the paragraph between the two sentences so each fact gets its own card"
            )
    return found


@dataclass(frozen=True)
class Uncarded:
    """A sentence whose new key facts its paragraph's card doesn't show (uncarded_facts())."""

    paragraph: int  # 0-based
    index: int  # the sentence's index among its paragraph's sentences()
    sentence: str
    facts: tuple[tuple[str, str], ...]  # key_facts() no earlier sentence said


def uncarded(script: Script) -> list[Uncarded]:
    """uncarded_facts()'s rule as data, in text order."""
    found = []
    said_before: set[tuple] = set()
    for i, p in enumerate(script.paragraphs):
        if p.tag or p.details or p.share or p.flirt:
            continue
        for k, sentence in enumerate(sentences(p.display)):
            facts = key_facts(sentence)
            keys = {fact_key(kind, word) for kind, word in facts}
            facts = [(kind, word) for kind, word in facts if fact_key(kind, word) not in said_before]
            said_before |= keys
            if facts and (p.fx is None or not any(p.fx.template in FACT_CARDS[kind] for kind, _ in facts)):
                found.append(Uncarded(i, k, sentence, tuple(facts)))
    return found


# ---- The fact cards' repair ------------------------------------------------------------------
