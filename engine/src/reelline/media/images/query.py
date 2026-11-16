"""What an @image query asks for: a generic scene or named entities.

parse_query() finds the named entities in a query (English, as punch-up writes it):
- runs of capitalized words ("Bank of Georgia", "OpenAI", "ECO.Georgia", "TSU"),
  joined by QUERY["connectors"] ("of", "the", ...); a run that isn't a known name
  (ENTITY_ALIASES) splits at "and" ("OpenAI and Anthropic logos" is two, "Georgia
  Innovation and Technology Agency" one). A run of only QUERY["generic_capitalized"]
  words isn't a name ("English language classroom", "calendar October", "AI chat on
  laptop"), and a trailing place after another name only says where it is ("Varna
  Bulgaria" is Varna). Known names count in lowercase too ("tsu campus").
A query with an entity is named, one without is generic (config.SOURCE_ORDER), and a
logo query (QUERY["logo_words"]) asks for an entity's mark.

names_entity() is the exact match: one of the entity's forms (its name or an alias)
appears as a whole phrase in a text (a Commons file's title or categories, a manual
add's tags, a script, a brief), case-insensitively; a Georgian form matches the words
it starts (inflections). "Tbilisi State University" is in "Ivane Javakhishvili Tbilisi
State University 1st building" but not in "Georgian Technical University, Building 0".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from reelline.media.images.config import ENTITY_ALIASES, GENERIC_WORDS, QUERY, STOPWORDS

_WORD = re.compile(r"[^\W_]+")


def name_words(text: str) -> tuple[str, ...]:
    """Lowercase words for name matching (letters and digits, any script; stopwords kept:
    "University of Georgia" must not match "... University, Georgia")."""
    return tuple(_WORD.findall(text.lower()))


def content_words(text: str) -> set[str]:
    """A generic query's words that say what a picture shows (no STOPWORDS or
    GENERIC_WORDS), for library overlap."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and w not in GENERIC_WORDS}


@lru_cache(maxsize=1)
def _known() -> dict[tuple[str, ...], str]:
    """Every form of every known name (as words) -> its canonical name."""
    out = {}
    for canonical, aliases in ENTITY_ALIASES.items():
        for form in (canonical, *aliases):
            out[name_words(form)] = canonical
    return out


def entity_forms(entity: str) -> tuple[tuple[str, ...], ...]:
    """The entity's name and aliases, as words."""
    canonical = _known().get(name_words(entity), entity)
    forms = {name_words(canonical)} | {name_words(a) for a in ENTITY_ALIASES.get(canonical, ())}
    return tuple(sorted(f for f in forms if f))


def _word_matches(form_word: str, text_word: str) -> bool:
    if form_word == text_word:
        return True
    # A non-Latin stem matches its inflected forms.
    return not form_word.isascii() and text_word.startswith(form_word)


def names_entity(text: str, entity: str) -> bool:
    """One of the entity's forms appears in `text` as a whole phrase."""
    words = name_words(text)
    for form in entity_forms(entity):
        n = len(form)
        for i in range(len(words) - n + 1):
            if all(_word_matches(f, w) for f, w in zip(form, words[i : i + n], strict=False)):
                return True
    return False


@dataclass(frozen=True)
class Query:
    text: str
    entities: tuple[str, ...]  # canonical names, lowercase ("tbilisi state university")
    logo: bool  # asks for a mark (QUERY["logo_words"])

    @property
    def named(self) -> bool:
        return bool(self.entities)

    @property
    def kind(self) -> str:
        """config.SOURCE_ORDER's key."""
        return "named" if self.named else "generic"


def _is_capitalized(token: str) -> bool:
    return any(c.isupper() for c in token)


def _part_entities(tokens: list[str]) -> list[str]:
    """The entities in one capitalized run (its tokens as lowercase words, connectors kept)."""
    known = _known()
    words = tuple(w for t in tokens for w in t.split())
    if words in known:
        return [known[words]]
    if "and" in tokens:
        at = tokens.index("and")
        return _part_entities(tokens[:at]) + _part_entities(tokens[at + 1 :])
    while tokens and tokens[-1] in QUERY["connectors"]:
        tokens = tokens[:-1]
    if all(w in QUERY["generic_capitalized"] or w in QUERY["connectors"] for t in tokens for w in t.split()):
        return []
    if len(tokens) > 1 and tokens[-1] in QUERY["trailing_places"] and tokens[-2] not in QUERY["connectors"]:
        tokens = tokens[:-1]
    words = tuple(w for t in tokens for w in t.split())
    return [known.get(words, " ".join(words))]


def parse_query(text: str) -> Query:
    tokens = text.split()
    lower = [" ".join(name_words(t)) for t in tokens]
    entities: list[str] = []
    # 1. Runs of capitalized words, joined by connectors and "and" (a known name like
    # "Georgia Innovation and Technology Agency" is one; otherwise "and" splits them).
    runs: list[list[int]] = []
    current: list[int] = []
    for i, token in enumerate(tokens):
        joins = lower[i] in (*QUERY["connectors"], "and")
        if _is_capitalized(token) and lower[i]:
            if not current:
                runs.append(current)
            current.append(i)
        elif current and joins and i + 1 < len(tokens) and _is_capitalized(tokens[i + 1]):
            current.append(i)
        else:
            current = []
    in_run = {i for run in runs for i in run}
    for run in runs:
        entities += [e for e in _part_entities([lower[i] for i in run]) if e not in entities]
    # 2. Known names written in lowercase ("tsu campus").
    known = _known()
    longest = max((len(form) for form in known), default=0)
    i = 0
    while i < len(tokens):
        for n in range(min(longest, len(tokens) - i), 0, -1):
            span = range(i, i + n)
            words = name_words(" ".join(tokens[i : i + n]))
            if words in known and not in_run.intersection(span):
                if known[words] not in entities:
                    entities.append(known[words])
                i += n
                break
        else:
            i += 1
    words = set(name_words(text))
    return Query(text, tuple(entities), bool(words & set(QUERY["logo_words"])))


def unnamed_entities(query: Query, context: str) -> list[str]:
    """The query's entities that `context` (the script and brief) doesn't name."""
    return [e for e in query.entities if not names_entity(context, e)]
