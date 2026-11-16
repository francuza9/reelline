"""parse(): script text to a Script."""

from __future__ import annotations

import bisect
import re

from reelline import language
from reelline.media.script_markup.base import (
    BLANK_LINE,
    DETAILS,
    FLIRT,
    GROUP,
    MORE_IMAGES,
    PARAGRAPH_SEP,
    SHARE,
    TAG_LINE,
    MarkupError,
)
from reelline.media.script_markup.checks import _check_details, _check_flirt, _check_fx, _check_share
from reelline.media.script_markup.directives import _directives, _has_latin, _split_cover
from reelline.media.script_markup.fx import fx_token, parse_fx
from reelline.media.script_markup.images import image_anchors, parse_image, parse_more_images
from reelline.media.script_markup.model import DisplayToken, Paragraph, Script, _Segment
from reelline.media.script_markup.style import _check_not_smug, _check_style, opening_emotion, relative_day_words
from reelline.media.script_markup.tags import (
    _check_no_leading_tag,
    _check_tag_paragraph,
    _check_tags,
    _overlapping,
    _paragraph_segments,
    _token_pieces,
    validate_spoken,
)
from reelline.media.script_markup.timing import _strip_punct


def parse(
    source: str,
    *,
    require_tag: bool = True,
    flirt: bool | None = None,
    details: bool | None = None,
    share: bool | None = None,
) -> Script:
    """Parse reel script markup. See the module docstring; require_tag=False parses a
    fragment that doesn't end with the tag paragraph; flirt True / False requires exactly
    one / no @flirt paragraph (None: at most one); details the same for @details, share for
    @share."""
    cover, source = _split_cover(source.strip())
    blocks = [b for b in BLANK_LINE.split(source.strip()) if b.strip()]
    if not blocks:
        raise MarkupError("Script is empty")

    segments: list[_Segment] = []
    paragraphs: list[Paragraph] = []
    para_starts: list[int] = []  # display offset of each paragraph
    d_pos = s_pos = 0
    for n, block in enumerate(blocks, start=1):
        if n > 1:
            d_pos += len(PARAGRAPH_SEP)
            s_pos += len(PARAGRAPH_SEP)
        directives, text = _directives(block, n, cover is not None)
        is_tag = require_tag and n == len(blocks)
        if is_tag:
            _check_tag_paragraph(directives, text, n)
            if n == 1:
                raise MarkupError(f"The script is only the tag paragraph ({TAG_LINE!r}); the story goes before it")
        opening = opening_emotion(directives["style"]) if n == 1 and "style" in directives else None
        if "style" in directives and opening is None:
            _check_style(directives["style"], n)
            if n == 1:
                _check_not_smug(directives["style"])
        if "|" in directives.get("image", ""):
            raise MarkupError(
                f"Paragraph {n}: the first @image starts with the paragraph; a `| word` is for the further @image lines"
            )
        image = parse_image(directives["image"], n) if "image" in directives else None
        more_images = parse_more_images(image, directives.get(MORE_IMAGES, ""), n)
        fx = parse_fx(directives["fx"], n) if "fx" in directives else None
        _check_tags(text, n)
        if n == 1:
            _check_no_leading_tag(text)
        tts = GROUP.sub(lambda m: m.group(2), text)
        plain = re.sub(r" +", " ", re.sub(r"\s*<[^<>]*>", "", text)).strip()
        if not plain:
            raise MarkupError(f"Paragraph {n} has no text")
        para_segments = _paragraph_segments(plain, n)
        para_starts.append(d_pos)
        for seg in para_segments:
            segments.append(_Segment(seg.display, seg.spoken, seg.is_group, d_pos, s_pos))
            d_pos += len(seg.display)
            s_pos += len(seg.spoken)
        paragraphs.append(
            Paragraph(
                display="".join(s.display for s in para_segments),
                spoken="".join(s.spoken for s in para_segments),
                tts=tts,
                style=None if opening else directives.get("style"),
                card=directives.get("card"),
                image=image,
                more_images=more_images,
                tag=is_tag,
                opening=opening,
                flirt=FLIRT in directives,
                details=DETAILS in directives,
                share=SHARE in directives,
                fx=fx,
            )
        )

    display = PARAGRAPH_SEP.join(p.display for p in paragraphs)
    matches = list(re.finditer(r"\S+", display))
    tokens = []
    for k, m in enumerate(matches):
        paragraph = bisect.bisect_right(para_starts, m.start()) - 1
        glue = False
        if k + 1 < len(matches):
            nxt = matches[k + 1]
            same_paragraph = bisect.bisect_right(para_starts, nxt.start()) - 1 == paragraph
            groups = {id(s) for s in _overlapping(m.start(), m.end(), segments) if s.is_group}
            shared_group = any(id(s) in groups for s in _overlapping(nxt.start(), nxt.end(), segments))
            glue = same_paragraph and (shared_group or (_has_latin(m.group()) and _has_latin(nxt.group())))
        tokens.append(DisplayToken(m.group(), m.start(), paragraph, _token_pieces(m.start(), m.end(), segments), glue))
    phrases = frozenset(
        tuple(_strip_punct(w) for w in s.display.split()) for s in segments if s.is_group and len(s.display.split()) > 1
    )
    script = Script(tuple(paragraphs), tuple(tokens), phrases, cover)
    _check_flirt(paragraphs, flirt, require_tag)
    _check_details(paragraphs, details, require_tag)
    _check_share(paragraphs, share)
    _check_fx(paragraphs, require_tag)
    for n, p in enumerate(paragraphs):
        if p.fx is not None:
            fx_token(script, n)
        image_anchors(script, n)
    validate_spoken(script.spoken)
    relative = relative_day_words(script.display)
    if relative:
        raise MarkupError(
            f"Relative day words {', '.join(map(repr, dict.fromkeys(relative)))}: the reel is made "
            f"before the day it's published; write the date itself ({language.current().date_example}), never today, "
            "tomorrow, yesterday or this week"
        )
    return script
