"""@image directives, their anchors and slots."""

from __future__ import annotations

from dataclasses import dataclass

from reelline.media.script_markup.base import (
    _CLAUSE_CLOSERS,
    _CLAUSE_END,
    _IMAGE_ID,
    _IMAGE_WORD,
    IMAGE_ID_LEN,
    IMAGE_LIB_PREFIX,
    IMAGE_MAX_PER_PARAGRAPH,
    IMAGE_MAX_WORDS,
    IMAGE_NONE,
    MarkupError,
)
from reelline.media.script_markup.model import ImageDirective, ImageSlot, Script
from reelline.media.script_markup.timing import _strip_punct


def parse_more_images(first: ImageDirective | None, values: str, n: int = 0) -> tuple[ImageSlot, ...]:
    """The extra `<query> | <word>` @image values (one per line) of a paragraph whose first
    @image is `first`; MarkupError for a bad one."""
    slots = []
    for value in values.split("\n") if values else ():
        query, bar, anchor = (part.strip() for part in value.partition("|"))
        if not bar or not anchor or len(anchor.split()) != 1:
            raise MarkupError(
                f"Paragraph {n}: a further @image line is `<query> | <word>` (the word the "
                f"picture changes at, one word of the paragraph); got {value!r}"
            )
        directive = parse_image(query, n)
        if directive.kind == "none":
            raise MarkupError(f"Paragraph {n}: a further @image can't be {IMAGE_NONE!r}; the previous picture holds")
        slots.append(ImageSlot(directive, anchor))
    if slots and (first is None or first.kind == "none"):
        raise MarkupError(
            f"Paragraph {n}: further @image lines need a first @image (a query or lib: pin) "
            "without a `| word`, before them"
        )
    if len(slots) + 1 > IMAGE_MAX_PER_PARAGRAPH:
        raise MarkupError(f"Paragraph {n} has {len(slots) + 1} @image lines, max {IMAGE_MAX_PER_PARAGRAPH}")
    return tuple(slots)


def parse_image(value: str, n: int = 0) -> ImageDirective:
    """An @image value -> ImageDirective; MarkupError for anything else. `n` is the
    1-based paragraph for the message (0: no paragraph context)."""
    where = f"Paragraph {n}: " if n else ""
    value = " ".join(value.split())
    if value.lower() == IMAGE_NONE:
        if value != IMAGE_NONE:
            raise MarkupError(f"{where}@image {value!r}: write it as {IMAGE_NONE!r}")
        return ImageDirective("none")
    if value.lower().startswith(IMAGE_LIB_PREFIX.rstrip(":")) and ":" in value:
        image_id = value.split(":", 1)[1].strip()
        if not value.startswith(IMAGE_LIB_PREFIX) or not _IMAGE_ID.fullmatch(image_id):
            raise MarkupError(
                f"{where}@image {value!r}: a library pin is {IMAGE_LIB_PREFIX}<id>, "
                f"id = {IMAGE_ID_LEN} lowercase hex digits (scripts/images.py list)"
            )
        return ImageDirective("lib", image_id)
    if not value.isascii():
        bad = sorted({ch for ch in value if not ch.isascii()})
        raise MarkupError(f"{where}@image {value!r}: the query must be English ASCII, found {''.join(bad)!r}")
    words = value.split()
    bad_words = [w for w in words if not _IMAGE_WORD.fullmatch(w)]
    if bad_words:
        raise MarkupError(
            f"{where}@image {value!r}: query words are letters/digits (with ' & . - inside), "
            f"got {', '.join(map(repr, bad_words))}"
        )
    if len(words) > IMAGE_MAX_WORDS:
        raise MarkupError(f"{where}@image has {len(words)} words, max {IMAGE_MAX_WORDS}: {value!r}")
    if not any(ch.isalpha() for ch in value):
        raise MarkupError(f"{where}@image {value!r}: the query has no letters")
    return ImageDirective("query", value)


def _starts_clause(script: Script, i: int) -> bool:
    """Token i is the first of its paragraph's sentence or clause: the token before it (same
    paragraph) ends with clause punctuation, closing quotes aside."""
    if i == 0 or script.tokens[i - 1].paragraph != script.tokens[i].paragraph:
        return True
    before = script.tokens[i - 1].text.rstrip(_CLAUSE_CLOSERS)
    return bool(before) and before[-1] in _CLAUSE_END


def image_anchors(script: Script, paragraph: int) -> list[int]:
    """Index in script.tokens where each extra picture of the 0-based paragraph starts:
    the first token equal to its anchor (punctuation at the ends ignored) that starts a
    clause and isn't the paragraph's first word, after the previous picture's. MarkupError
    for an anchor that isn't one."""
    out: list[int] = []
    floor = script.first_token(paragraph)
    for slot in script.paragraphs[paragraph].more_images:
        hit = next(
            (
                i
                for i in range(floor + 1, len(script.tokens))
                if script.tokens[i].paragraph == paragraph
                and _strip_punct(script.tokens[i].text) == slot.anchor
                and _starts_clause(script, i)
            ),
            None,
        )
        if hit is None:
            words = [_strip_punct(t.text) for t in script.tokens if t.paragraph == paragraph]
            raise MarkupError(
                f"Paragraph {paragraph + 1}: @image anchor {slot.anchor!r} isn't a word that starts a clause or "
                f"sentence after the previous picture (the word before it must end with , . ! ? ; : or ...; "
                f"words: {', '.join(words)})"
            )
        out.append(hit)
        floor = hit
    return out


@dataclass(frozen=True)
class SlotRef:
    """One wall-TV picture: paragraph (1-based), slot (0 = the paragraph's first picture),
    its directive and the token it starts at."""

    paragraph: int
    slot: int
    directive: ImageDirective | None
    token: int


def image_slots(script: Script) -> list[SlotRef]:
    """Every picture slot of the script in text order. A paragraph without @image has one
    slot with directive None."""
    out = []
    for n, p in enumerate(script.paragraphs):
        out.append(SlotRef(n + 1, 0, p.image, script.first_token(n)))
        for k, (slot, token) in enumerate(zip(p.more_images, image_anchors(script, n), strict=False), start=1):
            out.append(SlotRef(n + 1, k, slot.directive, token))
    return out
