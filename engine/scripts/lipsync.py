"""Mouth cues for a WAV file, as Rhubarb-style JSON: {"metadata": {...}, "mouthCues": [...]}.

Usage:
  python scripts/lipsync.py <input.wav> <out.json> (--text TEXT | --text-file PATH)
  python scripts/lipsync.py <input.wav> <out.json> --method rhubarb

--method align (default): character-level forced alignment of the known script
text (scripts/align.py), mapped to mouth shapes by reelline/media/visemes.py.

--method rhubarb (fallback): Rhubarb Lip Sync, phonetic recognizer. If Rhubarb
rejects the WAV format, the file is converted with ffmpeg to 16-bit PCM mono
and Rhubarb is run again; the conversion is reported on stdout.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from reelline.paths import SCRIPTS_DIR, TOOLS_DIR

sys.path.insert(0, str(SCRIPTS_DIR))

from align import AlignError, add_text_args, align, read_text_arg
from reelline.media.visemes import letters_to_cues

RHUBARB = TOOLS_DIR / "rhubarb" / "rhubarb"
METHODS = ("align", "rhubarb")

# Substrings of Rhubarb error messages that indicate an unreadable/unsupported WAV.
FORMAT_ERROR_MARKERS = ("format", "wave", "riff", "sample", "codec")


class LipsyncError(RuntimeError):
    """Raised when lip sync (Rhubarb, ffmpeg or the aligner) fails."""


def _rhubarb(wav: Path, out_json: Path) -> subprocess.CompletedProcess[str]:
    cmd = [str(RHUBARB), "-r", "phonetic", "-f", "json", "-o", str(out_json), str(wav)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _is_format_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in FORMAT_ERROR_MARKERS)


def _to_pcm16_mono(src: Path, dst: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise LipsyncError("ffmpeg not found on PATH; needed to convert the WAV for Rhubarb")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise LipsyncError(f"ffmpeg conversion failed (exit {result.returncode}):\n{result.stderr}")


def _probe_duration(wav: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(wav),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise LipsyncError(f"ffprobe failed on {wav}:\n{result.stderr}")
    return float(result.stdout.strip())


def run_rhubarb(wav: Path, out_json: Path) -> dict:
    if not RHUBARB.is_file():
        raise LipsyncError(f"Rhubarb binary not found: {RHUBARB} (see tools/README.md)")

    result = _rhubarb(wav, out_json)
    if result.returncode != 0 and _is_format_error(result.stderr):
        print(
            "NOTE: Rhubarb rejected the WAV format; converting to 16-bit PCM mono "
            f"with ffmpeg and retrying.\nRhubarb said:\n{result.stderr.strip()}"
        )
        with tempfile.TemporaryDirectory() as tmp:
            converted = Path(tmp) / "pcm16_mono.wav"
            _to_pcm16_mono(wav, converted)
            result = _rhubarb(converted, out_json)

    if result.returncode != 0:
        raise LipsyncError(
            f"Rhubarb failed (exit {result.returncode}):\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return json.loads(out_json.read_text(encoding="utf-8"))


def run_align(wav: Path, out_json: Path, text: str, letters_json: Path | None = None) -> dict:
    try:
        letters = align(wav, text)
    except AlignError as exc:
        raise LipsyncError(f"Forced alignment failed: {exc}") from exc
    return write_cues(wav, out_json, letters, letters_json)


def write_cues(wav: Path, out_json: Path, letters: list[dict], letters_json: Path | None = None) -> dict:
    """Mouth cues for `wav` from already aligned letters (written to `letters_json` if
    given); same output as run_align."""
    if letters_json is not None:
        letters_json.write_text(json.dumps(letters, ensure_ascii=False, indent=1), encoding="utf-8")
    duration = _probe_duration(wav)
    data = {
        "metadata": {"soundFile": str(wav), "duration": duration, "method": "align"},
        "mouthCues": letters_to_cues(letters, duration),
    }
    out_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def run_lipsync(
    wav: Path,
    out_json: Path,
    method: str = "align",
    text: str | None = None,
    letters_json: Path | None = None,
) -> dict:
    """Write mouth cues for `wav` to `out_json` and return the parsed JSON.

    With method "align", `letters_json` (if given) also gets the per-letter alignment.
    """
    if method not in METHODS:
        raise LipsyncError(f"Unknown method {method!r}; expected one of {METHODS}")
    if not wav.is_file():
        raise LipsyncError(f"Input WAV not found: {wav}")
    out_json.parent.mkdir(parents=True, exist_ok=True)

    if method == "rhubarb":
        return run_rhubarb(wav, out_json)
    if not text or not text.strip():
        raise LipsyncError("--method align needs the script text (--text or --text-file)")
    return run_align(wav, out_json, text, letters_json)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("out_json", type=Path)
    parser.add_argument("--method", choices=METHODS, default="align")
    add_text_args(parser, required=False)
    args = parser.parse_args()

    text = read_text_arg(args) if args.method == "align" else None
    try:
        data = run_lipsync(args.input_wav, args.out_json, args.method, text)
    except LipsyncError as exc:
        sys.exit(f"FAIL: {exc}")
    print(f"OK: wrote {args.out_json} ({len(data['mouthCues'])} cues, method {args.method})")


if __name__ == "__main__":
    main()
