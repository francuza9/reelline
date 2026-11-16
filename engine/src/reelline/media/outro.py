"""The outro: the end card, a still summary of the opportunity over the host's last line.

The numbers and the card's fields live in remotion/src/outro.json, shared with the
Remotion side (remotion/src/EndCard.tsx draws the card) and reelline/pipeline/endcard.py (which
writes the facts). This module loads and checks them, places the outro in time and reads
a script's facts file.

Since 2026-10-25 the card comes in over speech (card_start()): leadS before the first word
of the line outro.json startAt names ("tag": the tag line, the default; "share": the share
line), never before the previous word's end. The host's voice goes on under it; the captions
stop where it starts (captions_before(): the card is a frame of its own, a caption would sit
on its facts), and after the voice ends it holds holdS with music only (outro_window()).

The facts file is <script>.endcard.json next to the script (reelline/pipeline/script.py writes it
for every script it makes): {"lines": {field: text}}, a field left out when the facts
don't support it. card_problems() is the parser both sides use: known fields only, each a
non-empty line of at most its maxChars, no markup, and "apply" a bare domain. The music
under the voice is ducked as everywhere; once the voice has ended it is raised by
musicLiftDb (ramped over musicLiftS) and fades out over the last musicFadeS seconds
(reelline/media/music.py mix(), lift and fade_out_s).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reelline import language
from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.media.script_markup import Script
from reelline.paths import graphics_file

OUTRO_JSON = graphics_file(conf().channel.graphics_dir, "outro.json")
# The facts file's suffix, next to the script (<name>.txt -> <name>.endcard.json).
FACTS_SUFFIX = ".endcard.json"
# "apply" is a bare domain: lowercase labels, a dot between them, no scheme, path or www.
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}")
# Characters that never belong on the card: script markup, and dashes other than hyphens.
NOT_ON_CARD = "{}|<>@—–"
# The lines the card can come in on (outro.json startAt).
START_AT = ("tag", "share")
# What a model writes for a missing fact instead of leaving the line out.
PLACEHOLDERS = ("not stated", "n/a", "unknown", "none", "null", *language.current().missing_fact_words)


class OutroError(ReellineError, ValueError):
    """Raised for a bad outro.json or a bad facts file."""


@dataclass(frozen=True)
class Field:
    key: str
    max_chars: int


@dataclass(frozen=True)
class Outro:
    start_at: str  # START_AT: the line the card comes in on
    lead_s: float  # the card opens this long before that line's first word
    hold_s: float  # the card's time after the voice has ended (music only)
    fields: tuple[Field, ...]
    footer: tuple[str, ...]
    music_fade_s: float
    music_lift_db: float
    music_lift_s: float

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    def max_chars(self, key: str) -> int:
        return next(f.max_chars for f in self.fields if f.key == key)


def load_outro(path: Path = OUTRO_JSON) -> Outro:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
        outro = Outro(
            str(spec["startAt"]),
            float(spec["leadS"]),
            float(spec["holdS"]),
            tuple(Field(str(f["key"]), int(f["maxChars"])) for f in spec["fields"]),
            tuple(spec["footer"]),
            float(spec["musicFadeS"]),
            float(spec["musicLiftDb"]),
            float(spec["musicLiftS"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise OutroError(f"Can't read {path}: {exc}") from exc
    if outro.start_at not in START_AT:
        raise OutroError(f"{path.name}: startAt must be one of {', '.join(START_AT)}, got {outro.start_at!r}")
    if not 0 <= outro.lead_s <= 1:
        raise OutroError(f"{path.name}: leadS must be in [0, 1] s, got {outro.lead_s}")
    if outro.hold_s <= 0:
        raise OutroError(f"{path.name}: holdS must be > 0, got {outro.hold_s}")
    if not outro.fields or len(set(outro.keys)) != len(outro.keys) or any(f.max_chars <= 0 for f in outro.fields):
        raise OutroError(f"{path.name}: fields must be distinct keys with maxChars > 0, got {list(outro.fields)}")
    if not outro.footer or not all(isinstance(line, str) and line.strip() for line in outro.footer):
        raise OutroError(f"{path.name}: footer must be non-empty strings, got {list(outro.footer)}")
    if any("—" in line for line in outro.footer):
        raise OutroError(f"{path.name}: use hyphens (-), never em dashes, in {list(outro.footer)}")
    if not 0 < outro.music_fade_s <= outro.hold_s:
        raise OutroError(f"{path.name}: musicFadeS must be in (0, holdS = {outro.hold_s}], got {outro.music_fade_s}")
    if not 0 <= outro.music_lift_db <= 6:
        raise OutroError(f"{path.name}: musicLiftDb must be in [0, 6] dB, got {outro.music_lift_db}")
    if not 0 < outro.music_lift_s <= outro.hold_s - outro.music_fade_s + 1e-9:
        raise OutroError(f"{path.name}: musicLiftS must be in (0, holdS - musicFadeS], got {outro.music_lift_s}")
    return outro


def card_line(script: Script, outro: Outro) -> tuple[int, str]:
    """(0-based paragraph, "tag" | "share") the card comes in on: outro.start_at's line; the
    tag when it's "share" and the script has no share line."""
    share = next((n for n, p in enumerate(script.paragraphs) if p.share), None)
    if outro.start_at == "share" and share is not None:
        return share, "share"
    return len(script.paragraphs) - 1, "tag"


def card_start(script: Script, words: list[dict[str, Any]], outro: Outro) -> tuple[float, str]:
    """(when the card starts opening, the line it comes in on): lead_s before the first
    word of card_line()'s paragraph, never before the end of the word before it. `words`
    are the timed display tokens (reelline/media/script_markup.py time_tokens()), one per
    script.tokens entry."""
    n, line = card_line(script, outro)
    timed = [(t.paragraph, w) for t, w in zip(script.tokens, words, strict=False) if w["start"] is not None]
    first = next((w["start"] for p, w in timed if p == n), None)
    if first is None:
        raise OutroError(f"The {line} line (paragraph {n + 1}) has no aligned word for the end card to start at")
    before = max((w["end"] for p, w in timed if p < n), default=0.0)
    return round(max(first - outro.lead_s, before, 0.0), 3), line


def outro_window(start: float, speech_end: float, outro: Outro, line: str = "tag") -> dict[str, Any]:
    """{"start", "speechEnd", "end", "startAt"} of the outro in seconds: the card from
    `start` (card_start()), the voice track's end, and the reel's end hold_s after it."""
    if start >= speech_end:
        raise OutroError(f"The end card's start {start:.3f}s is not before the voice's end {speech_end:.3f}s")
    return {
        "start": round(start, 3),
        "speechEnd": round(speech_end, 3),
        "end": round(speech_end + outro.hold_s, 3),
        "startAt": line,
    }


def captions_before(captions: list[dict[str, Any]], start: float) -> list[dict[str, Any]]:
    """The caption chunks Remotion shows: those starting before the end card (`start`),
    the last one cut to end there, any word starting at or after it left out. The card is
    a frame of its own: a caption over it would sit on its facts (captions.json keeps them all)."""
    out = []
    for c in captions:
        if c["chunk_start"] >= start - 1e-6:
            break
        lines = [[w for w in line if w["start"] < start - 1e-6] for line in c["lines"]]
        lines = [line for line in lines if line]
        if lines:
            out.append({**c, "chunk_end": round(min(c["chunk_end"], start), 3), "lines": lines})
    return out


# ---- The facts ----------------------------------------------------------------------------


def facts_path(script_path: Path) -> Path:
    """<name>.endcard.json next to the script <name>.txt."""
    return script_path.with_suffix(FACTS_SUFFIX)


def card_problems(lines: dict, outro: Outro) -> list[str]:
    """What's wrong with the card's lines ({field: text}), [] if nothing: unknown fields,
    a line that isn't a non-empty single line, longer than its maxChars, with markup or a
    dash other than a hyphen, a placeholder for a missing fact, or an "apply" that isn't a
    bare domain."""
    if not isinstance(lines, dict):
        return [f"the lines must be an object {{field: text}}, got {type(lines).__name__}"]
    problems = []
    unknown = [k for k in lines if k not in outro.keys]
    if unknown:
        problems.append(f"unknown field(s) {', '.join(unknown)}; the card has {', '.join(outro.keys)}")
    for key in (k for k in outro.keys if k in lines):
        text = lines[key]
        if not isinstance(text, str) or not text.strip() or "\n" in text or text != " ".join(text.split()):
            problems.append(f"{key}: must be one non-empty line without extra spaces, got {text!r}")
            continue
        if len(text) > outro.max_chars(key):
            problems.append(f"{key} is {len(text)} characters, at most {outro.max_chars(key)}: „{text}“")
        bad = sorted({ch for ch in text if ch in NOT_ON_CARD})
        if bad:
            problems.append(
                f"{key}: no markup or dashes other than a hyphen on the card, found {''.join(bad)!r}: „{text}“"
            )
        if text.casefold().strip(" .") in PLACEHOLDERS:
            problems.append(f"{key}: „{text}“ is a placeholder; leave a missing fact out")
        if key == "apply" and not DOMAIN.fullmatch(text):
            problems.append(
                f"apply must be a bare domain (edu.aris.ge: lowercase, no https://, www. or path), got „{text}“"
            )
        elif key == "apply" and text.startswith("www."):
            problems.append(f"apply: no www. („{text.removeprefix('www.')}“)")
    return problems


def ordered(lines: dict[str, str], outro: Outro) -> list[dict[str, str]]:
    """The lines in the card's order: [{"field", "text"}] (Remotion's outro.facts)."""
    return [{"field": k, "text": lines[k]} for k in outro.keys if lines.get(k)]


def load_facts(path: Path, outro: Outro) -> dict[str, str] | None:
    """The card's lines from a facts file ({field: text}); None without the file.
    OutroError for a file that can't be read or whose lines break card_problems()."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        lines = data["lines"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise OutroError(f"Can't read {path}: {exc}") from exc
    problems = card_problems(lines, outro)
    if problems:
        raise OutroError(f"{path.name}: " + "; ".join(problems))
    return {k: lines[k] for k in outro.keys if k in lines}


def write_facts(path: Path, lines: dict[str, str], outro: Outro, **meta) -> Path:
    """Write a facts file (failing on lines card_problems() rejects); `meta` goes alongside
    the lines (the script id, the lines left out and why)."""
    problems = card_problems(lines, outro)
    if problems:
        raise OutroError(f"{path.name}: " + "; ".join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"lines": {k: lines[k] for k in outro.keys if k in lines}, **meta}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path
