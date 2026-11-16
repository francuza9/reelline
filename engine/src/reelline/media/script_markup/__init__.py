"""Reel script markup: what the captions show vs. what the voice says.

A script may start with a cover line, then a blank line:

  @cover: <headline>                the cover image's headline (see parse_cover):
                                    1-COVER_MAX_WORDS words, display text only

@cover anywhere else, or given twice, raises MarkupError. Without it the cover uses
paragraph 1's @card (cover_headline()).

The last paragraph is the tag: exactly TAG_LINE, alone, with no directives, groups or
vocal tags. Anything else (a missing tag, a different wording, a directive on it, a
script that is only the tag) raises MarkupError. Its Paragraph has tag=True; its audio
can be pinned (voice/tag.wav, scripts/tag_audio.py). parse(..., require_tag=False)
skips the rule, for markup fragments.

The rest is paragraphs separated by blank lines. Each paragraph may start with
directive lines, then has its text:

  @style: <English delivery note>   nuance added to the TTS style for this paragraph;
                                    at most STYLE_MAX_WORDS words, none from STYLE_BANNED.
                                    On paragraph 1 it may instead be exactly one of
                                    OPENING_EMOTIONS: the opening emotion, voiced on the
                                    first sentence only (Paragraph.opening, style None);
                                    OPENING_WORDS are allowed in no other @style.
                                    Paragraph 1's @style is never smug (SMUG_FORMS);
                                    later paragraphs may be
  @card: <headline text>            headline card from this paragraph's first word
  @image: <query> | lib:<id> | none  wall-TV image from this paragraph's first word:
                                    a search query (English, ASCII, 1-IMAGE_MAX_WORDS
                                    words), a pinned library image, or explicitly none.
                                    More @image lines in the same paragraph are further
                                    pictures, each `<query> | <word>`: it replaces the
                                    TV's picture from <word>, a display word of this
                                    paragraph that starts a clause or sentence (the word
                                    before it ends with , . ! ? ; : or ...); at most
                                    IMAGE_MAX_PER_PARAGRAPH pictures in a paragraph, the
                                    anchors in text order, and only after a first @image
                                    that is a query or a lib: pin (Paragraph.more_images;
                                    image_slots() lists them all)
  @flirt                            (no value) this paragraph is the flirt line
                                    (prompts/punchup.md): at most one in a script, never
                                    paragraph 1, the closing jab (the paragraph before the
                                    tag) or the tag; its display text one sentence of
                                    1-FLIRT_MAX_WORDS words; parse(..., flirt=True)
                                    requires exactly one, flirt=False none (Paragraph.flirt)
  @details                          (no value) this paragraph is the details line, the host
                                    pointing to the end card (prompts/punchup.md): at most
                                    one, never paragraph 1, the closing jab or the tag, not
                                    the flirt line; no @card or @image (the TV keeps the
                                    picture before it); its display text one sentence of
                                    1-DETAILS_MAX_WORDS words; parse(..., details=True)
                                    requires exactly one, details=False none
                                    (Paragraph.details)
  @share                            (no value) this paragraph is the share line, the host
                                    telling the viewer to send the video to a specific
                                    friend, as a joke (prompts/punchup.md): at most one,
                                    never paragraph 1 or the tag, not the details or the
                                    flirt line, never right before or after the details
                                    line; it may be the closing jab; no @card or @fx; its
                                    display text 1-SHARE_MAX_SENTENCES sentences of
                                    1-SHARE_MAX_WORDS words in all; parse(..., share=True)
                                    requires exactly one, share=False none (Paragraph.share)
  @fx: <template> <value> [| <word>]  a fact graphic (channel docs/fx.md): a card pops out of the
                                    TV with one of reelline/media/fx.py TEMPLATES showing the
                                    value, from <word> (a display word of this paragraph,
                                    punctuation at its ends ignored; else its first word).
                                    The value as the template takes it (reelline/media/fx.py
                                    parse_value(), strict). At most FX maxPerReel in a
                                    script, never on the closing jab, the tag, the
                                    details, share or flirt line, never on two paragraphs
                                    in a row (Paragraph.fx; fx_token() its start). On
                                    paragraph 1 it's the opening's stake (since punch-up
                                    19), the frame-0 card: out from the first frame
                                    (fx.json opening; its anchor doesn't move it)
  text with {display|spoken} groups and <vocal tags>

In the text:
- `{display|spoken}`: the display side is what captions and cards show ("12",
  "AI"), the spoken side is what TTS says and the aligner aligns ("twelve",
  "ay-eye" spelled out). Text outside braces is the same in both.
- `<tag>` from VOCAL_TAGS (e.g. <laugh>, <short pause>): sent to TTS verbatim,
  stripped (with the whitespace before it) from the display and spoken texts.
  Tags go outside groups; unknown tags raise MarkupError, and so does a tag before
  paragraph 1's first spoken word (the reel opens on a word, not a sound).
Whitespace runs within a paragraph collapse to one space.

Dates are absolute: the reel is made the evening before it's published (or earlier),
so relative day words (relative_day_words(): today, tomorrow, yesterday, this week...)
in the display text raise MarkupError.

parse() returns a Script: the cover headline (or None); per paragraph the display
text, the spoken text (the aligner's input), the TTS text (spoken + tags), style,
card and image; plus the display
tokens (whitespace-separated words of the display text, punctuation attached),
each with the spoken text it covers as Pieces:
- outside groups, a display character covers the same spoken character;
- a group's display words share the group's whole spoken time, split by their
  character counts ("AI Academy" over "ay-eye academy" spelled out: AI gets 2/9 of it).
A token is `glue`d to the next one when both are in the same group or both
contain Latin letters, so captions never split them apart. card_units() applies
the same rule to a headline card's text.

The spoken text must be pure Georgian: digits and Latin letters raise
MarkupError, so a script can't reach TTS with something the aligner can't align.

time_tokens() turns the pieces into word times given the aligner's letters
(scripts/align.py output for Script.spoken).
"""

from __future__ import annotations

from reelline.media.script_markup.base import (
    BARE,
    BLANK_LINE,
    COVER,
    COVER_MAX_WORDS,
    COVER_PUNCT,
    DEFAULT_OPENER_ANIMATION,
    DETAILS,
    DETAILS_MAX_WORDS,
    DIRECTIVE,
    DIRECTIVES,
    FLIRT,
    FLIRT_MAX_WORDS,
    GROUP,
    IMAGE_ID_LEN,
    IMAGE_LIB_PREFIX,
    IMAGE_MAX_PER_PARAGRAPH,
    IMAGE_MAX_WORDS,
    IMAGE_NONE,
    MARKUP_CHARS,
    MORE_IMAGES,
    OPENER_ANIMATIONS,
    OPENER_KEYWORDS,
    OPENING_EMOTIONS,
    OPENING_WORDS,
    PARAGRAPH_SEP,
    RELATIVE_DAY_PHRASES,
    RELATIVE_DAY_STEMS,
    RELATIVE_DAY_WORDS,
    SHARE,
    SHARE_MAX_SENTENCES,
    SHARE_MAX_WORDS,
    SMUG_FORMS,
    STYLE_BANNED,
    STYLE_MAX_WORDS,
    TAG,
    TAG_LINE,
    VOCAL_TAGS,
    MarkupError,
)
from reelline.media.script_markup.directives import cover_headline, parse_cover
from reelline.media.script_markup.fx import fx_token, parse_fx
from reelline.media.script_markup.images import SlotRef, image_anchors, image_slots, parse_image, parse_more_images
from reelline.media.script_markup.model import DisplayToken, ImageDirective, ImageSlot, Paragraph, Piece, Script
from reelline.media.script_markup.parse import parse
from reelline.media.script_markup.style import (
    opener_animation,
    opening_emotion,
    relative_day_words,
    split_first_sentence,
)
from reelline.media.script_markup.tags import validate_spoken
from reelline.media.script_markup.timing import card_units, find_token, time_tokens

__all__ = [
    "BARE",
    "BLANK_LINE",
    "COVER",
    "COVER_MAX_WORDS",
    "COVER_PUNCT",
    "DEFAULT_OPENER_ANIMATION",
    "DETAILS",
    "DETAILS_MAX_WORDS",
    "DIRECTIVE",
    "DIRECTIVES",
    "FLIRT",
    "FLIRT_MAX_WORDS",
    "GROUP",
    "IMAGE_ID_LEN",
    "IMAGE_LIB_PREFIX",
    "IMAGE_MAX_PER_PARAGRAPH",
    "IMAGE_MAX_WORDS",
    "IMAGE_NONE",
    "MARKUP_CHARS",
    "MORE_IMAGES",
    "OPENER_ANIMATIONS",
    "OPENER_KEYWORDS",
    "OPENING_EMOTIONS",
    "OPENING_WORDS",
    "PARAGRAPH_SEP",
    "RELATIVE_DAY_PHRASES",
    "RELATIVE_DAY_STEMS",
    "RELATIVE_DAY_WORDS",
    "SHARE",
    "SHARE_MAX_SENTENCES",
    "SHARE_MAX_WORDS",
    "SMUG_FORMS",
    "STYLE_BANNED",
    "STYLE_MAX_WORDS",
    "TAG",
    "TAG_LINE",
    "VOCAL_TAGS",
    "DisplayToken",
    "ImageDirective",
    "ImageSlot",
    "MarkupError",
    "Paragraph",
    "Piece",
    "Script",
    "SlotRef",
    "card_units",
    "cover_headline",
    "find_token",
    "fx_token",
    "image_anchors",
    "image_slots",
    "opener_animation",
    "opening_emotion",
    "parse",
    "parse_cover",
    "parse_fx",
    "parse_image",
    "parse_more_images",
    "relative_day_words",
    "split_first_sentence",
    "time_tokens",
    "validate_spoken",
]
