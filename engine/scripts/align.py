"""Character-level forced alignment of Georgian speech with Meta's MMS aligner.

Usage: python scripts/align.py <input.wav> <out.json> (--text TEXT | --text-file PATH)

Every Georgian letter of the script text is romanized on its own with uroman
(e.g. შ -> "sh"), the romanized characters are aligned to the audio with
torchaudio's MMS_FA pipeline, and the character spans are folded back onto the
original letters. Spaces and punctuation are not aligned; they become the pauses
between words.

Output: JSON list of {char, roman, index, start, end, score} per Georgian letter,
in text order. `index` is the letter's position in the text; `score` is the mean
CTC probability of the letter's characters. Times are seconds:
- start: the letter's CTC onset.
- end: the next letter's start within a word. CTC spans are one-frame spikes, so
  a word's last letter instead ends where the audio drops to near-silence
  (QUIET_DB below the loudest 20 ms window), capped at the next word's start.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import shutil
import subprocess
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

# The 33 letters of the modern Georgian (Mkhedruli) alphabet, U+10D0..U+10F0.
GEORGIAN_FIRST, GEORGIAN_LAST = 0x10D0, 0x10F0

# Speech/silence split for word-final letters: 20 ms RMS windows, quiet if this many
# dB below the loudest window.
ENERGY_WINDOW_S = 0.02
QUIET_DB = 30.0


class AlignError(RuntimeError):
    """Raised when the text or audio can't be aligned."""


def is_georgian_letter(ch: str) -> bool:
    return GEORGIAN_FIRST <= ord(ch) <= GEORGIAN_LAST


def _is_pause(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch)[0] in ("P", "Z")


@lru_cache(maxsize=1)
def _uroman():
    import uroman

    return uroman.Uroman()


@lru_cache(maxsize=1)
def _bundle():
    import torchaudio

    return torchaudio.pipelines.MMS_FA


@lru_cache(maxsize=1)
def _model():
    return _bundle().get_model(with_star=False).eval()


def release() -> None:
    """Give the aligner's memory back (the model is ~1.2 GB): the render calls this
    before Remotion, which needs the room (on the server, within the render slice's cap).
    The next align() loads the model again."""
    import ctypes
    import gc

    _model.cache_clear()
    _bundle.cache_clear()
    gc.collect()
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL("libc.so.6").malloc_trim(0)  # glibc keeps freed heap otherwise


def romanize_letters(text: str) -> list[dict[str, Any]]:
    """Romanize each Georgian letter separately, keeping its position in `text`.

    Raises AlignError for characters that are neither Georgian letters nor
    pauses (digits, Latin, obsolete Georgian letters), since those would be
    spoken but have no letter to map a mouth shape from.
    """
    labels = _bundle().get_dict(star=None)
    letters = []
    for index, ch in enumerate(text):
        if is_georgian_letter(ch):
            roman = _uroman().romanize_string(ch).lower()
            unknown = [c for c in roman if c not in labels or c == "-"]
            if not roman or unknown:
                raise AlignError(
                    f"Letter {ch!r} at {index} romanizes to {roman!r}, which the MMS aligner can't represent"
                )
            letters.append({"char": ch, "roman": roman, "index": index})
        elif not _is_pause(ch):
            raise AlignError(
                f"Unsupported character {ch!r} (U+{ord(ch):04X}) at {index}; "
                "write numbers and non-Georgian words out in Georgian letters"
            )
    if not letters:
        raise AlignError("Text contains no Georgian letters")
    return letters


def load_audio(wav: Path, sample_rate: int):
    """Decode any audio file to a (1, n) float tensor, mono, at `sample_rate`."""
    import torch

    if shutil.which("ffmpeg") is None:
        raise AlignError("ffmpeg not found on PATH; needed to decode audio")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(wav),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise AlignError(f"ffmpeg failed to decode {wav}:\n{result.stderr.decode(errors='replace')}")
    if not result.stdout:
        raise AlignError(f"No audio samples decoded from {wav}")
    samples = torch.frombuffer(bytearray(result.stdout), dtype=torch.int16)
    return (samples.float() / 32768.0).unsqueeze(0)


def _quiet_windows(waveform, sample_rate: int) -> list[bool]:
    """Per ENERGY_WINDOW_S window of `waveform`: True if it's near-silent."""
    hop = round(ENERGY_WINDOW_S * sample_rate)
    n = waveform.size(1) // hop
    rms = waveform[0, : n * hop].view(n, hop).pow(2).mean(dim=1).sqrt()
    threshold = rms.max() * 10 ** (-QUIET_DB / 20)
    return (rms < threshold).tolist()


def _set_ends(letters: list[dict[str, Any]], quiet: list[bool], duration: float) -> None:
    """Replace CTC span ends with sounding ends (see the module docstring)."""
    for i, letter in enumerate(letters):
        nxt = letters[i + 1] if i + 1 < len(letters) else None
        if nxt is not None and nxt["index"] == letter["index"] + 1:
            letter["end"] = nxt["start"]
            continue
        limit = nxt["start"] if nxt is not None else duration
        k = math.ceil(letter["end"] / ENERGY_WINDOW_S)
        while k < len(quiet) and k * ENERGY_WINDOW_S < limit and not quiet[k]:
            k += 1
        letter["end"] = min(max(k * ENERGY_WINDOW_S, letter["end"]), limit)


def align(wav: Path, text: str) -> list[dict[str, Any]]:
    """Align every Georgian letter of `text` to `wav`. See the module docstring."""
    import torch
    import torchaudio.functional as F

    if not wav.is_file():
        raise AlignError(f"Input WAV not found: {wav}")
    letters = romanize_letters(text)

    bundle = _bundle()
    labels = bundle.get_dict(star=None)
    tokens: list[int] = []
    owner: list[int] = []  # tokens[k] belongs to letters[owner[k]]
    for i, letter in enumerate(letters):
        for c in letter["roman"]:
            tokens.append(labels[c])
            owner.append(i)

    waveform = load_audio(wav, bundle.sample_rate)
    with torch.inference_mode():
        emission, _ = _model()(waveform)
    if emission.size(1) < len(tokens):
        raise AlignError(f"Audio too short for the text: {emission.size(1)} frames for {len(tokens)} characters")

    targets = torch.tensor([tokens], dtype=torch.int32)
    try:
        path, scores = F.forced_align(emission, targets, blank=labels["-"])
    except RuntimeError as exc:
        raise AlignError(f"forced_align failed: {exc}") from exc
    spans = F.merge_tokens(path[0], scores[0].exp(), blank=labels["-"])
    if len(spans) != len(tokens):
        raise AlignError(f"Expected {len(tokens)} character spans, got {len(spans)}")

    seconds_per_frame = waveform.size(1) / emission.size(1) / bundle.sample_rate
    out = [dict(letter, start=None, end=None, score=[]) for letter in letters]
    for span, i in zip(spans, owner, strict=False):
        entry = out[i]
        start = span.start * seconds_per_frame
        end = span.end * seconds_per_frame
        entry["start"] = start if entry["start"] is None else entry["start"]
        entry["end"] = end
        entry["score"].append(span.score)
    duration = waveform.size(1) / bundle.sample_rate
    _set_ends(out, _quiet_windows(waveform, bundle.sample_rate), duration)
    for entry in out:
        entry["score"] = round(sum(entry["score"]) / len(entry["score"]), 3)
        entry["start"] = round(entry["start"], 3)
        entry["end"] = round(entry["end"], 3)
    return out


def read_text_arg(args: argparse.Namespace) -> str:
    if args.text_file is not None:
        return args.text_file.read_text(encoding="utf-8")
    return args.text


def add_text_args(parser: argparse.ArgumentParser, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--text", help="script text spoken in the audio")
    group.add_argument("--text-file", type=Path, help="UTF-8 file with the script text")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("out_json", type=Path)
    add_text_args(parser, required=True)
    args = parser.parse_args()

    try:
        letters = align(args.input_wav, read_text_arg(args))
    except AlignError as exc:
        sys.exit(f"FAIL: {exc}")
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(letters, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: wrote {args.out_json} ({len(letters)} letters)")


if __name__ == "__main__":
    main()
