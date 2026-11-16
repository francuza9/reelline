"""Burned-in caption chunks from the script text and its letter alignment.

Usage: python scripts/captions.py <letters.json> <out.json> (--text TEXT | --text-file PATH)

<letters.json> is scripts/align.py output for the same text: per Georgian letter
{char, index, start, end}, where `index` points into the text.

Words are the whitespace-separated tokens of the text, punctuation attached. A
word runs from its first letter's start to its last letter's end. Words are
grouped into chunks of MIN_WORDS..MAX_WORDS that fit in MAX_LINES lines of about
MAX_LINE_CHARS characters (a single longer word still gets a line of its own):
- a chunk never runs past a sentence end (. ! ? …);
- otherwise it ends at the last comma-like punctuation it can reach;
- otherwise it takes as many words as fit, unless that would leave a single
  word stranded before the next punctuation.
Within a chunk, lines break after punctuation if possible, else as evenly as
possible. A chunk shows from its first word's start until the next chunk starts;
the last one stays TAIL_S past its last word.

One-line windows (chunk_words(..., one_line=...); render_reel.py passes the fact graphics'
stretches, reelline/media/fx.py caption_windows()): a chunk that would be on screen during one,
from its first word's start until the next chunk starts, gets at most one line (the
popped-out card reaches below a two-line caption's top).

Words with "glue": true stay with the next word: a glued run is never split
across chunks or lines and counts as one word for MIN_WORDS/MAX_WORDS, so a
chunk can hold more words than MAX_WORDS (see reelline/media/script_markup.py for what
gets glued).

Output: JSON list of
  {chunk_start, chunk_end, lines: [[{text, start, end}, ...], ...]}
in seconds.
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from align import add_text_args, read_text_arg


@dataclass(frozen=True)
class ChunkConfig:
    min_words: int = 2
    max_words: int = 4
    max_lines: int = 2
    max_line_chars: int = 16
    # How long the last chunk stays after its last word ends.
    tail_s: float = 0.4


CONFIG = ChunkConfig()

SENTENCE_END = ".!?…"
# Any trailing punctuation mark counts as a preferred break.
TRAILING_PUNCT = re.compile(r"[^\w\s]$")


class CaptionError(ValueError):
    """Raised when the alignment doesn't match the text."""


def _ends_sentence(word: str) -> bool:
    return word.rstrip("\"'»“”)")[-1:] in SENTENCE_END


def _ends_punct(word: str) -> bool:
    return bool(TRAILING_PUNCT.search(word))


def join_untimed(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold tokens without timing ({text, start: None, end: None}, e.g. a lone
    dash) into the previous word, or the next one if they come first. A folded
    token's "glue" (if any) becomes the word's glue."""
    words: list[dict[str, Any]] = []
    pending = ""  # untimed tokens before the first word
    for token in tokens:
        if token["start"] is None:
            if words:
                words[-1]["text"] += " " + token["text"]
                if "glue" in token:
                    words[-1]["glue"] = token["glue"]
            else:
                pending += token["text"] + " "
            continue
        word = {"text": pending + token["text"], "start": token["start"], "end": token["end"]}
        if "glue" in token:
            word["glue"] = token["glue"]
        words.append(word)
        pending = ""
    if not words:
        raise CaptionError("No aligned words in the text")
    return words


def build_words(text: str, letters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whitespace tokens of `text` with times from their aligned letters.

    A token with no aligned letters (e.g. a lone dash) is appended to the
    previous word, since it has no timing of its own.
    """
    for letter in letters:
        i = letter["index"]
        if not 0 <= i < len(text) or text[i] != letter["char"]:
            raise CaptionError(
                f"Letter {letter['char']!r} at index {i} doesn't match the text; "
                "was the alignment made from a different script?"
            )
    by_index = {letter["index"]: letter for letter in letters}

    tokens = []
    for match in re.finditer(r"\S+", text):
        timed = [by_index[i] for i in range(match.start(), match.end()) if i in by_index]
        tokens.append(
            {
                "text": match.group(),
                "start": timed[0]["start"] if timed else None,
                "end": timed[-1]["end"] if timed else None,
            }
        )
    return join_untimed(tokens)


def layout_lines(texts: list[str], cfg: ChunkConfig = CONFIG) -> list[int] | None:
    """Split word texts into the fewest lines that fit; returns line sizes in words.

    A line fits if it has at most cfg.max_line_chars characters or is a single
    word. Among splits with the same line count, prefer breaking after
    punctuation, then the shortest longest line. None if nothing fits.
    """
    n = len(texts)
    for n_lines in range(1, min(cfg.max_lines, n) + 1):
        best = None
        for cuts in itertools.combinations(range(1, n), n_lines - 1):
            bounds = (0, *cuts, n)
            lines = [texts[a:b] for a, b in zip(bounds, bounds[1:], strict=False)]
            lengths = [len(" ".join(line)) for line in lines]
            if any(size > cfg.max_line_chars and len(line) > 1 for size, line in zip(lengths, lines, strict=False)):
                continue
            punct_breaks = sum(_ends_punct(texts[c - 1]) for c in cuts)
            score = (-punct_breaks, max(lengths))
            if best is None or score < best[0]:
                best = (score, [b - a for a, b in zip(bounds, bounds[1:], strict=False)])
        if best is not None:
            return best[1]
    return None


def _chunk_end(texts: list[str], i: int, cfg: ChunkConfig) -> int:
    """End index (exclusive) of the chunk that starts at word i."""
    n = len(texts)
    # Hard limit: the chunk ends at the first sentence end.
    limit = min(i + cfg.max_words, n)
    for k in range(i, limit):
        if _ends_sentence(texts[k]):
            limit = k + 1
            break
    fitting = [j for j in range(i + 1, limit + 1) if layout_lines(texts[i:j], cfg) is not None]
    # A single word always fits (on its own line), so `fitting` starts with i + 1.
    full = [j for j in fitting if j - i >= cfg.min_words] or fitting

    at_punct = [j for j in full if _ends_punct(texts[j - 1])]
    if at_punct:
        return at_punct[-1]

    next_punct = next((k + 1 for k in range(i, n) if _ends_punct(texts[k])), n)
    no_orphan = [j for j in full if next_punct - j != 1]
    return (no_orphan or full)[-1]


def _glued_units(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Runs of words joined by "glue", in order; unglued words are runs of one."""
    units: list[list[dict[str, Any]]] = []
    run: list[dict[str, Any]] = []
    for w in words:
        run.append(w)
        if not w.get("glue"):
            units.append(run)
            run = []
    if run:
        units.append(run)
    return units


def _on_screen(units: list[list[dict[str, Any]]], i: int, j: int, cfg: ChunkConfig) -> tuple[float, float]:
    """When the chunk of units[i:j] shows: its first word's start until the next chunk's start
    (the last one until TAIL_S past its last word)."""
    end = units[j][0]["start"] if j < len(units) else units[j - 1][-1]["end"] + cfg.tail_s
    return units[i][0]["start"], end


def chunk_words(
    words: list[dict[str, Any]], cfg: ChunkConfig = CONFIG, one_line: list[tuple[float, float]] | tuple = ()
) -> list[dict[str, Any]]:
    """Caption chunks (the module docstring); a chunk on screen during one of the `one_line`
    (start, end) windows gets at most one line."""
    units = _glued_units(words)
    texts = [" ".join(w["text"] for w in unit) for unit in units]
    single = dataclasses.replace(cfg, max_lines=1)
    groups: list[list[list[dict[str, Any]]]] = []
    configs: list[ChunkConfig] = []
    i = 0
    while i < len(units):
        j, used = _chunk_end(texts, i, cfg), cfg
        start, end = _on_screen(units, i, j, cfg)
        if cfg.max_lines > 1 and any(a < end and b > start for a, b in one_line):
            j, used = _chunk_end(texts, i, single), single
        groups.append(units[i:j])
        configs.append(used)
        i = j

    chunks = []
    for k, group in enumerate(groups):
        sizes = layout_lines([" ".join(w["text"] for w in unit) for unit in group], configs[k])
        lines, pos = [], 0
        for size in sizes:
            lines.append(
                [
                    {"text": w["text"], "start": w["start"], "end": w["end"]}
                    for unit in group[pos : pos + size]
                    for w in unit
                ]
            )
            pos += size
        end = groups[k + 1][0][0]["start"] if k + 1 < len(groups) else group[-1][-1]["end"] + cfg.tail_s
        chunks.append(
            {
                "chunk_start": round(group[0][0]["start"], 3),
                "chunk_end": round(end, 3),
                "lines": lines,
            }
        )
    return chunks


def build_captions(text: str, letters: list[dict[str, Any]], cfg: ChunkConfig = CONFIG) -> list[dict[str, Any]]:
    """Caption chunks for `text` from its letter alignment. See the module docstring."""
    return chunk_words(build_words(text, letters), cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("letters_json", type=Path)
    parser.add_argument("out_json", type=Path)
    add_text_args(parser, required=True)
    args = parser.parse_args()

    letters = json.loads(args.letters_json.read_text(encoding="utf-8"))
    try:
        chunks = build_captions(read_text_arg(args), letters)
    except CaptionError as exc:
        sys.exit(f"FAIL: {exc}")
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(chunks, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: wrote {args.out_json} ({len(chunks)} chunks)")


if __name__ == "__main__":
    main()
