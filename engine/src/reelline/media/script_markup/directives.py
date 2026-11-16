"""Directive lines (@style, @card, @image, @cover, ...) split off a paragraph; the cover headline."""

from __future__ import annotations

import re
import unicodedata

from reelline import language
from reelline.media.script_markup.base import (
    BARE,
    COVER,
    COVER_MAX_WORDS,
    COVER_PUNCT,
    DETAILS,
    DIRECTIVE,
    DIRECTIVES,
    FLIRT,
    MARKUP_CHARS,
    MORE_IMAGES,
    SHARE,
    MarkupError,
)
from reelline.media.script_markup.model import Script


def _context(text: str, i: int, width: int = 15) -> str:
    return text[max(0, i - width) : i + width + 1].replace("\n", " ")


def _has_latin(text: str) -> bool:
    return any(ch.isalpha() and "LATIN" in unicodedata.name(ch, "") for ch in text)


def _directives(block: str, n: int, has_cover: bool) -> tuple[dict[str, str], str]:
    """Split a paragraph block into its @directives and its text (whitespace collapsed)."""
    found: dict[str, str] = {}
    text_lines = []
    for line in block.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("@"):
            text_lines.append(line)
            continue
        bare = next((b for b in BARE if stripped == f"@{b}" or stripped.startswith(f"@{b}:")), None)
        if bare is not None:
            if stripped != f"@{bare}":
                raise MarkupError(f"Paragraph {n}: @{bare} takes no value; write the line as @{bare} alone")
            if bare in found:
                raise MarkupError(f"Paragraph {n}: @{bare} given twice")
            found[bare] = ""
            continue
        m = DIRECTIVE.fullmatch(stripped)
        if m is not None and m.group(1) == COVER:
            raise MarkupError(
                f"Paragraph {n}: @{COVER} given twice; it's the script's first line only"
                if has_cover
                else f"Paragraph {n}: @{COVER} must be the script's first line, before the first paragraph"
            )
        if m is None or m.group(1) not in DIRECTIVES:
            raise MarkupError(
                f"Paragraph {n}: unknown directive {stripped!r}; expected one of "
                + ", ".join(f"@{d}: ..." for d in DIRECTIVES)
                + f", @{FLIRT}, @{DETAILS} or @{SHARE}"
            )
        name, value = m.group(1), m.group(2).strip()
        if not value:
            raise MarkupError(f"Paragraph {n}: empty @{name}")
        if name == "image" and name in found:
            found[MORE_IMAGES] = found.get(MORE_IMAGES, "") + ("\n" if MORE_IMAGES in found else "") + value
            continue
        if name in found:
            raise MarkupError(f"Paragraph {n}: @{name} given twice")
        found[name] = value
    return found, re.sub(r"\s+", " ", " ".join(text_lines)).strip()


def _is_georgian(ch: str) -> bool:
    return language.current().script_char_wide(ch)


def parse_cover(value: str) -> str:
    """An @cover value -> the headline (whitespace normalized); MarkupError if it isn't
    1-COVER_MAX_WORDS words of Georgian letters, digits, Latin and COVER_PUNCT."""
    value = " ".join(value.split())
    if any(ch in MARKUP_CHARS for ch in value):
        raise MarkupError(f"@{COVER} {value!r}: no {{display|spoken}} braces, the cover is display text only")
    if "<" in value or ">" in value:
        raise MarkupError(f"@{COVER} {value!r}: no vocal tags, the cover is display text only")
    if "\u2014" in value:
        raise MarkupError(f"@{COVER} {value!r}: use hyphens (-), never em dashes")
    bad = sorted(
        {
            ch
            for ch in value
            if not (ch == " " or _is_georgian(ch) or (ch.isascii() and ch.isalnum()) or ch in COVER_PUNCT)
        }
    )
    if bad:
        raise MarkupError(
            f"@{COVER} {value!r}: only Georgian letters, digits, Latin words and basic punctuation, "
            f"found {''.join(bad)!r}"
        )
    words = [w for w in value.split() if any(ch.isalnum() for ch in w)]
    if not 1 <= len(words) <= COVER_MAX_WORDS:
        raise MarkupError(f"@{COVER} has {len(words)} words, expected 1-{COVER_MAX_WORDS}: {value!r}")
    return value


def _split_cover(source: str) -> tuple[str | None, str]:
    """(the @cover value, or None; the rest of the source). @cover must be the first
    line and be followed by a blank line."""
    first, _, rest = source.partition("\n")
    m = DIRECTIVE.fullmatch(first.strip())
    if m is None or m.group(1) != COVER:
        return None, source
    if not m.group(2).strip():
        raise MarkupError(f"Empty @{COVER}")
    if rest.split("\n", 1)[0].strip():
        raise MarkupError(f"@{COVER} must be followed by a blank line, then the first paragraph")
    return parse_cover(m.group(2)), rest


def cover_headline(script: Script) -> tuple[str, str]:
    """(headline, where it came from) for the cover image: the @cover line, else
    paragraph 1's @card. MarkupError if the script has neither."""
    if script.cover is not None:
        return script.cover, f"@{COVER}"
    card = script.paragraphs[0].card
    if card is None:
        raise MarkupError(f"No cover headline: add an @{COVER}: first line, or an @card to paragraph 1")
    return card, "paragraph 1 @card"
