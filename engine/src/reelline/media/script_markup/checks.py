"""Whole-script checks for the flirt, details, share and fx paragraphs."""

from __future__ import annotations

from reelline.media.fx import config as fx_config
from reelline.media.script_markup.base import (
    _FIRST_SENTENCE_END,
    DETAILS,
    DETAILS_MAX_WORDS,
    FLIRT,
    FLIRT_MAX_WORDS,
    SHARE,
    SHARE_MAX_SENTENCES,
    SHARE_MAX_WORDS,
    MarkupError,
)
from reelline.media.script_markup.model import Paragraph


def _check_flirt(paragraphs: list, flirt: bool | None, require_tag: bool) -> None:
    """At most one @flirt paragraph, never paragraph 1, the closing jab (the one before the
    tag) or the tag, its display text one sentence of 1-FLIRT_MAX_WORDS words; flirt True:
    exactly one, False: none."""
    at = [n for n, p in enumerate(paragraphs, 1) if p.flirt]
    if len(at) > 1:
        raise MarkupError(f"@{FLIRT} on paragraphs {', '.join(map(str, at))}: a script has one flirt line at most")
    if flirt is True and not at:
        raise MarkupError(
            f"This video has a flirt line: one paragraph must start with @{FLIRT} (its own paragraph, "
            "after paragraph 1 and before the closing jab)"
        )
    if flirt is False and at:
        raise MarkupError(f"This video has no flirt line: paragraph {at[0]} has @{FLIRT}; take it out (no flirting)")
    if not at:
        return
    n = at[0]
    if n == 1:
        raise MarkupError(f"Paragraph 1 can't be the flirt line (@{FLIRT}): it's the hook")
    if require_tag and n >= len(paragraphs) - 1:
        raise MarkupError(
            f"Paragraph {n} can't be the flirt line (@{FLIRT}): it's the closing jab; put the flirt "
            "line earlier, mid-flow"
        )
    _one_sentence(paragraphs[n - 1], n, "flirt line", FLIRT_MAX_WORDS)


def _one_sentence(paragraph: Paragraph, n: int, what: str, max_words: int, max_sentences: int = 1) -> None:
    """The paragraph's display text is one sentence (at most max_sentences) of 1-max_words words."""
    line = paragraph.display.strip()
    words = [w for w in line.split() if any(c.isalnum() for c in w)]
    count = len(_FIRST_SENTENCE_END.findall(line)) + 1
    if max_sentences == 1:
        shape, sentences = "one short sentence", " and more than one sentence" if count > 1 else ""
    else:
        shape, sentences = (
            f"at most {max_sentences} short sentences",
            f" and {count} sentences" if count > max_sentences else "",
        )
    if sentences or not 1 <= len(words) <= max_words:
        raise MarkupError(
            f"Paragraph {n}: the {what} is {shape} of at most {max_words} "
            f"words, found {len(words)} words{sentences}: {line!r}"
        )


def _check_fx(paragraphs: list, require_tag: bool) -> None:
    """At most fx.json reel.maxPerReel @fx paragraphs, never the closing jab (the one before
    the tag; allowed with reel.beforeTag), the tag, the details, share or flirt line, never two
    in a row (allowed with reel.consecutive). Paragraph 1 may have one (punch-up 19: the opening's
    stake; fx_problems() in reelline/pipeline/script.py wants it), drawn as the frame-0 card (reelline/media/fx.py
    planned()). The straight style (reelline/media/style.py) sets both: it has no closing jab, and a card on
    every key fact."""
    reel = fx_config()["reel"]
    at = [n for n, p in enumerate(paragraphs, 1) if p.fx is not None]
    most = int(reel["maxPerReel"])
    if len(at) > most:
        raise MarkupError(f"@fx on {len(at)} paragraphs ({', '.join(map(str, at))}): at most {most} in a script")
    for n in at:
        p = paragraphs[n - 1]
        where = (
            "the closing jab"
            if require_tag and n == len(paragraphs) - 1 and not reel["beforeTag"]
            else "the details line"
            if p.details
            else "the flirt line"
            if p.flirt
            else "the share line"
            if p.share
            else None
        )
        if where:
            raise MarkupError(
                f"Paragraph {n}: no @fx on {where}; put the fact graphic on the paragraph that states the fact"
            )
    pairs = [(a, b) for a, b in zip(at, at[1:], strict=False) if b == a + 1]
    if pairs and not reel["consecutive"]:
        raise MarkupError(
            f"@fx on paragraphs {pairs[0][0]} and {pairs[0][1]}: never two in a row; keep at least "
            "one paragraph between them"
        )


def _check_details(paragraphs: list, details: bool | None, require_tag: bool) -> None:
    """At most one @details paragraph, never paragraph 1, the closing jab (the one before the
    tag), the tag or the flirt line, without @card or @image, its display text one sentence
    of 1-DETAILS_MAX_WORDS words; details True: exactly one, False: none."""
    at = [n for n, p in enumerate(paragraphs, 1) if p.details]
    if len(at) > 1:
        raise MarkupError(f"@{DETAILS} on paragraphs {', '.join(map(str, at))}: a script has one details line at most")
    if details is True and not at:
        raise MarkupError(
            f"This video needs its details line: one paragraph must start with @{DETAILS} (its own "
            "paragraph, right after the plain explanation, before the conditions)"
        )
    if details is False and at:
        raise MarkupError(f"This script has no details line: paragraph {at[0]} has @{DETAILS}; take it out")
    if not at:
        return
    n = at[0]
    p = paragraphs[n - 1]
    if n == 1:
        raise MarkupError(f"Paragraph 1 can't be the details line (@{DETAILS}): it's the hook")
    if require_tag and n >= len(paragraphs) - 1:
        raise MarkupError(
            f"Paragraph {n} can't be the details line (@{DETAILS}): it's the closing jab; put it "
            "right after the plain explanation"
        )
    if p.flirt:
        raise MarkupError(f"Paragraph {n} has both @{FLIRT} and @{DETAILS}: each is its own paragraph")
    if p.card is not None or p.image is not None:
        raise MarkupError(
            f"Paragraph {n}: the details line (@{DETAILS}) takes no @card or @image; the TV keeps the picture before it"
        )
    _one_sentence(p, n, "details line", DETAILS_MAX_WORDS)


def _check_share(paragraphs: list, share: bool | None) -> None:
    """At most one @share paragraph, never paragraph 1 or the tag (a directive there fails
    first), not the details or the flirt line, never next to the details line, without @card
    (the parser's @fx check covers @fx), its display text 1-SHARE_MAX_SENTENCES sentences of
    1-SHARE_MAX_WORDS words; share True: exactly one, False: none. It may be the closing jab."""
    at = [n for n, p in enumerate(paragraphs, 1) if p.share]
    if len(at) > 1:
        raise MarkupError(f"@{SHARE} on paragraphs {', '.join(map(str, at))}: a script has one share line at most")
    if share is True and not at:
        raise MarkupError(
            f"This video needs its share line: one paragraph must start with @{SHARE} (its own "
            "paragraph, in the second half, after the conditions: before the closing jab or as it)"
        )
    if share is False and at:
        raise MarkupError(f"This script has no share line: paragraph {at[0]} has @{SHARE}; take it out")
    if not at:
        return
    n = at[0]
    p = paragraphs[n - 1]
    if n == 1:
        raise MarkupError(f"Paragraph 1 can't be the share line (@{SHARE}): it's the hook")
    for other, what in ((p.flirt, FLIRT), (p.details, DETAILS)):
        if other:
            raise MarkupError(f"Paragraph {n} has both @{what} and @{SHARE}: each is its own paragraph")
    near = [m for m in (n - 1, n + 1) if 1 <= m <= len(paragraphs) and paragraphs[m - 1].details]
    if near:
        raise MarkupError(
            f"Paragraph {n}: the share line (@{SHARE}) is right next to the details line (paragraph "
            f"{near[0]}); put it later, after the conditions"
        )
    if p.card is not None:
        raise MarkupError(f"Paragraph {n}: the share line (@{SHARE}) takes no @card; it's a joke, not a fact")
    _one_sentence(p, n, "share line", SHARE_MAX_WORDS, SHARE_MAX_SENTENCES)
