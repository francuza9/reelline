"""The parsed script: pieces, display tokens, image directives, paragraphs, the Script."""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from reelline.media.fx import FxDirective
from reelline.media.script_markup.base import IMAGE_LIB_PREFIX, IMAGE_NONE, PARAGRAPH_SEP


@dataclass(frozen=True)
class Piece:
    """Part of a display token's time: the letters of spoken[spoken_start:spoken_end]
    span some time [t0, t1]; this piece is [t0 + frac_start * (t1 - t0),
    t0 + frac_end * (t1 - t0)]."""

    spoken_start: int
    spoken_end: int
    frac_start: float = 0.0
    frac_end: float = 1.0


@dataclass(frozen=True)
class DisplayToken:
    text: str
    display_start: int  # position in Script.display
    paragraph: int  # 0-based
    pieces: tuple[Piece, ...]
    glue: bool = False  # captions keep this token with the next one


@dataclass(frozen=True)
class ImageDirective:
    """A paragraph's @image value: kind "query" (value = the query), "lib" (value =
    the library image id) or "none" (value = "")."""

    kind: str
    value: str = ""

    @property
    def raw(self) -> str:
        """The directive's value as written (whitespace normalized)."""
        if self.kind == "none":
            return IMAGE_NONE
        return IMAGE_LIB_PREFIX + self.value if self.kind == "lib" else self.value


@dataclass(frozen=True)
class ImageSlot:
    """A further picture within a paragraph (an extra @image line): `directive` shows from
    `anchor`, a display word of the paragraph (punctuation at its ends ignored)."""

    directive: ImageDirective
    anchor: str


@dataclass(frozen=True)
class Paragraph:
    display: str
    spoken: str  # aligner input: groups' spoken sides, no tags
    tts: str  # TTS input: spoken plus vocal tags
    style: str | None = None
    card: str | None = None
    image: ImageDirective | None = None
    more_images: tuple[ImageSlot, ...] = ()  # pictures after the first, in text order
    tag: bool = False  # the closing TAG_LINE paragraph
    opening: str | None = None  # paragraph 1's opening emotion (one of OPENING_EMOTIONS)
    flirt: bool = False  # the flirt line's paragraph (@flirt)
    details: bool = False  # the details line's paragraph (@details)
    share: bool = False  # the share line's paragraph (@share)
    fx: FxDirective | None = None  # the paragraph's fact graphic (@fx)


@dataclass(frozen=True)
class Script:
    paragraphs: tuple[Paragraph, ...]
    tokens: tuple[DisplayToken, ...]
    # Display sides of multi-word groups, as words without edge punctuation.
    group_phrases: frozenset[tuple[str, ...]] = frozenset()
    # The @cover headline (whitespace normalized), None without one.
    cover: str | None = None

    @property
    def spoken(self) -> str:
        return PARAGRAPH_SEP.join(p.spoken for p in self.paragraphs)

    @property
    def opening(self) -> str | None:
        """The opening emotion (paragraph 1's), or None."""
        return self.paragraphs[0].opening

    @property
    def display(self) -> str:
        return PARAGRAPH_SEP.join(p.display for p in self.paragraphs)

    def paragraph_at_spoken(self, index: int) -> int:
        """0-based paragraph of position `index` in self.spoken."""
        starts, pos = [], 0
        for p in self.paragraphs:
            starts.append(pos)
            pos += len(p.spoken) + len(PARAGRAPH_SEP)
        return bisect.bisect_right(starts, index) - 1

    def first_token(self, paragraph: int) -> int:
        """Index in self.tokens of the paragraph's first display token."""
        return next(i for i, t in enumerate(self.tokens) if t.paragraph == paragraph)


@dataclass(frozen=True)
class _Segment:
    display: str
    spoken: str
    is_group: bool
    display_start: int = 0
    spoken_start: int = 0
