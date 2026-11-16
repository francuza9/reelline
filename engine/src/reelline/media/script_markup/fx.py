"""@fx directives: parsing and the token a card starts on."""

from __future__ import annotations

from reelline.media.fx import FxDirective, FxError
from reelline.media.fx import parse_directive as parse_fx_value
from reelline.media.script_markup.base import MarkupError
from reelline.media.script_markup.model import Script
from reelline.media.script_markup.timing import _strip_punct


def parse_fx(value: str, n: int = 0) -> FxDirective:
    """An @fx value -> FxDirective; MarkupError for anything a template can't show. `n` is
    the 1-based paragraph for the message (0: no paragraph context)."""
    try:
        return parse_fx_value(value)
    except FxError as exc:
        raise MarkupError(f"{f'Paragraph {n}: ' if n else ''}@fx {' '.join(value.split())!r}: {exc}") from None


def fx_token(script: Script, paragraph: int) -> int:
    """Index in script.tokens where the 0-based paragraph's @fx starts: its anchor word (the
    first display token of the paragraph equal to it, punctuation at the ends ignored), else
    the paragraph's first token. MarkupError for an anchor the paragraph doesn't have."""
    fx = script.paragraphs[paragraph].fx
    first = script.first_token(paragraph)
    if fx is None or fx.anchor is None:
        return first
    hit = next(
        (
            i
            for i in range(first, len(script.tokens))
            if script.tokens[i].paragraph == paragraph and _strip_punct(script.tokens[i].text) == fx.anchor
        ),
        None,
    )
    if hit is None:
        words = [_strip_punct(t.text) for t in script.tokens if t.paragraph == paragraph]
        raise MarkupError(
            f"Paragraph {paragraph + 1}: @fx anchor {fx.anchor!r} isn't one of its words ({', '.join(words)})"
        )
    return hit
