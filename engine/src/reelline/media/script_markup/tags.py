"""Vocal tags and {display|spoken} groups: a paragraph's segments and token pieces."""

from __future__ import annotations

import re

from reelline import language
from reelline.media.script_markup.base import GROUP, MARKUP_CHARS, TAG, TAG_LINE, VOCAL_TAGS, MarkupError
from reelline.media.script_markup.directives import _context, _has_latin
from reelline.media.script_markup.model import Piece, _Segment


def _check_tag_paragraph(directives: dict[str, str], text: str, n: int) -> None:
    """The last paragraph must be exactly TAG_LINE, with no directives."""
    if text != TAG_LINE:
        shown = text if len(text) <= 60 else f"...{text[-57:]}"
        raise MarkupError(
            f"The script must end with the tag paragraph, exactly {TAG_LINE!r} alone after a "
            f"blank line; the last paragraph (paragraph {n}) is {shown!r}"
        )
    if directives:
        raise MarkupError(
            f"The tag paragraph (paragraph {n}) takes no directives, found "
            + ", ".join(f"@{name}" for name in directives)
        )


def _check_tags(text: str, n: int) -> None:
    for g in GROUP.finditer(text):
        if "<" in g.group() or ">" in g.group():
            raise MarkupError(f"Paragraph {n}: vocal tags go outside groups, found {g.group()!r}")
    for m in TAG.finditer(text):
        if m.group(1) not in VOCAL_TAGS:
            raise MarkupError(
                f"Paragraph {n}: unknown vocal tag {m.group()!r}; allowed: "
                + " ".join(f"<{t}>" for t in sorted(VOCAL_TAGS))
            )
    rest = TAG.sub("", text)
    for ch in "<>":
        if ch in rest:
            raise MarkupError(f"Paragraph {n}: stray {ch!r} near: {_context(rest, rest.index(ch))!r}")


def _check_no_leading_tag(text: str) -> None:
    """Paragraph 1 must not have a vocal tag before its first spoken word."""
    m = TAG.search(text)
    first_word = next((i for i, ch in enumerate(TAG.sub(lambda t: " " * len(t.group()), text)) if ch.isalnum()), None)
    if m is not None and (first_word is None or m.start() < first_word):
        raise MarkupError(
            f"Paragraph 1: vocal tag {m.group()} before the first spoken word; the reel opens on a "
            "word (move the tag after it, or drop it)"
        )


def _paragraph_segments(para: str, n: int) -> list[_Segment]:
    """Plain and group segments of one paragraph's tag-free text."""
    segments: list[_Segment] = []
    pos = 0
    for match in [*GROUP.finditer(para), None]:
        plain = para[pos : match.start() if match else len(para)]
        stray = next((c for c in plain if c in MARKUP_CHARS), None)
        if stray is not None:
            raise MarkupError(
                f"Paragraph {n}: stray {stray!r} outside a {{display|spoken}} group "
                f"near: {_context(para, pos + plain.index(stray))!r}"
            )
        if plain:
            segments.append(_Segment(plain, plain, False))
        if match is None:
            break
        display, spoken = match.group(1), match.group(2)
        for side, value in (("display", display), ("spoken", spoken)):
            if not value.strip():
                raise MarkupError(f"Paragraph {n}: empty {side} side in {match.group()!r}")
            if value != value.strip():
                raise MarkupError(f"Paragraph {n}: {side} side of {match.group()!r} starts or ends with a space")
        segments.append(_Segment(display, spoken, True))
        pos = match.end()
    return segments


def validate_spoken(spoken: str) -> None:
    """Raise MarkupError listing every digit and Latin letter in the spoken text (none for a language
    whose TTS reads them: spoken_latin_ok)."""
    if language.current().spoken_latin_ok:
        return
    bad = [(i, ch) for i, ch in enumerate(spoken) if ch.isdigit() or _has_latin(ch)]
    if bad:
        found = "; ".join(f"{ch!r} in {_context(spoken, i)!r}" for i, ch in bad[:10])
        more = f" (+{len(bad) - 10} more)" if len(bad) > 10 else ""
        raise MarkupError(
            f"Spoken text has {len(bad)} digit/Latin character(s): {found}{more}. "
            "Wrap them as {display|spoken} with the spoken side in Georgian letters."
        )


def _group_word_pieces(seg: _Segment) -> dict[int, Piece]:
    """Display-word start offset (within the group) -> its share of the group's time."""
    words = list(re.finditer(r"\S+", seg.display))
    total = sum(len(w.group()) for w in words)
    spoken_end = seg.spoken_start + len(seg.spoken)
    out, done = {}, 0
    for w in words:
        out[w.start()] = Piece(seg.spoken_start, spoken_end, done / total, (done + len(w.group())) / total)
        done += len(w.group())
    return out


def _overlapping(start: int, end: int, segments: list[_Segment]) -> list[_Segment]:
    return [s for s in segments if max(start, s.display_start) < min(end, s.display_start + len(s.display))]


def _token_pieces(start: int, end: int, segments: list[_Segment]) -> tuple[Piece, ...]:
    pieces: list[Piece] = []
    for seg in _overlapping(start, end, segments):
        lo = max(start, seg.display_start)
        hi = min(end, seg.display_start + len(seg.display))
        if not seg.is_group:
            offset = seg.spoken_start - seg.display_start
            pieces.append(Piece(lo + offset, hi + offset))
            continue
        # Tokens never contain whitespace, so the token covers whole group words.
        for word_start, piece in _group_word_pieces(seg).items():
            if lo <= seg.display_start + word_start < hi:
                pieces.append(piece)
    return tuple(pieces)
