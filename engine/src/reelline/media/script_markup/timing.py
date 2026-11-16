"""Word times from the aligner's letters, token lookup, card units."""

from __future__ import annotations

import bisect
import unicodedata
from typing import Any

from reelline.media.script_markup.base import MarkupError
from reelline.media.script_markup.directives import _has_latin
from reelline.media.script_markup.model import Script


def time_tokens(script: Script, letters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{text, start, end, glue}] per display token; start/end are None for a token
    with no aligned letters (e.g. a lone dash). `letters` must be aligned to
    script.spoken."""
    spoken = script.spoken
    for letter in letters:
        i = letter["index"]
        if not 0 <= i < len(spoken) or spoken[i] != letter["char"]:
            raise MarkupError(
                f"Letter {letter['char']!r} at index {i} doesn't match the spoken text; "
                "was the alignment made from a different script?"
            )
    indices = sorted(letter["index"] for letter in letters)
    by_index = {letter["index"]: letter for letter in letters}

    def span(a: int, b: int) -> tuple[float, float] | None:
        inside = indices[bisect.bisect_left(indices, a) : bisect.bisect_left(indices, b)]
        if not inside:
            return None
        return by_index[inside[0]]["start"], by_index[inside[-1]]["end"]

    out = []
    for token in script.tokens:
        times = []
        for piece in token.pieces:
            s = span(piece.spoken_start, piece.spoken_end)
            if s is not None:
                t0, t1 = s
                times.append((t0 + piece.frac_start * (t1 - t0), t0 + piece.frac_end * (t1 - t0)))
        start = round(times[0][0], 3) if times else None
        end = round(times[-1][1], 3) if times else None
        out.append({"text": token.text, "start": start, "end": end, "glue": token.glue})
    return out


def _strip_punct(word: str) -> str:
    def is_punct(c):
        return unicodedata.category(c)[0] == "P"

    lo, hi = 0, len(word)
    while lo < hi and is_punct(word[lo]):
        lo += 1
    while hi > lo and is_punct(word[hi - 1]):
        hi -= 1
    return word[lo:hi]


def find_token(script: Script, word: str, occurrence: int = 1) -> int:
    """Index in script.tokens of the `occurrence`-th (1-based) token equal to `word`,
    ignoring punctuation at either end of the token."""
    hits = [i for i, t in enumerate(script.tokens) if _strip_punct(t.text) == word]
    if len(hits) < occurrence:
        raise MarkupError(f"Display word {word!r} occurs {len(hits)} time(s), wanted occurrence {occurrence}")
    return hits[occurrence - 1]


def card_units(script: Script, text: str) -> list[str]:
    """A headline card's words, with the ones that must share a line joined into
    one unit: runs of words containing Latin letters, and the script's multi-word
    group phrases (edge punctuation ignored). The renderer wraps between units."""
    words = text.split()
    glue = [_has_latin(a) and _has_latin(b) for a, b in zip(words, words[1:], strict=False)] + [False]
    keys = [_strip_punct(w) for w in words]
    for phrase in script.group_phrases:
        for k in range(len(words) - len(phrase) + 1):
            if tuple(keys[k : k + len(phrase)]) == phrase:
                for j in range(k, k + len(phrase) - 1):
                    glue[j] = True
    units, run = [], []
    for word, g in zip(words, glue, strict=False):
        run.append(word)
        if not g:
            units.append(" ".join(run))
            run = []
    return units
