"""ffprobe and ffmpeg measurements: stream facts, loudness, silences."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from reelline.pipeline import qa as _qa
from reelline.pipeline.qa.base import QaError

VIDEO_TAGS = ("color_space", "color_primaries", "color_transfer", "color_range")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(mp4: Path) -> dict:
    """{duration, video: {codec, width, height, fps, pix_fmt, color_*, mbps} | None, audio: bool};
    mbps is the video stream's bitrate (None when ffprobe doesn't report one)."""
    r = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,bit_rate,"
            + ",".join(VIDEO_TAGS)
            + ":format=duration",
            "-of",
            "json",
            str(mp4),
        ]
    )
    if r.returncode != 0:
        raise QaError(f"ffprobe failed on {mp4}: {r.stderr.strip()[-300:]}")
    info = json.loads(r.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    out = {
        "duration": float(info.get("format", {}).get("duration") or 0.0),
        "video": None,
        "audio": any(s.get("codec_type") == "audio" for s in streams),
    }
    if video:
        num, _, den = video.get("r_frame_rate", "0/1").partition("/")
        rate = video.get("bit_rate")
        out["video"] = {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": round(float(num) / float(den or 1), 3) if float(den or 1) else 0.0,
            "pix_fmt": video.get("pix_fmt"),
            **{k: video.get(k) for k in VIDEO_TAGS},
            "mbps": round(int(rate) / 1e6, 2) if rate and rate.isdigit() else None,
        }
    return out


def audio_levels(mp4: Path) -> dict:
    """{"lufs": integrated loudness, "tp": true peak (dBTP)} of the file's audio."""
    r = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(mp4),
            "-map",
            "0:a:0",
            "-af",
            "ebur128=peak=true:framelog=quiet",
            "-f",
            "null",
            "-",
        ]
    )
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", r.stderr)
    if r.returncode != 0 or not found or not peak:
        raise QaError(f"loudness measurement failed on {mp4}: {r.stderr.strip()[-300:]}")
    return {"lufs": float(found[-1]), "tp": float(peak[-1])}


def loudness(mp4: Path) -> float:
    """Integrated loudness (LUFS) of the file's audio."""
    return _qa.audio_levels(mp4)["lufs"]


def silences(wav: Path, noise_db: float, min_s: float) -> list[tuple[float, float]]:
    """(start, length) of every silence below `noise_db` lasting at least `min_s`."""
    r = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(wav),
            "-af",
            f"silencedetect=n={noise_db}dB:d={min_s}",
            "-f",
            "null",
            "-",
        ]
    )
    if r.returncode != 0:
        raise QaError(f"silence detection failed on {wav}: {r.stderr.strip()[-300:]}")
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    lengths = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", r.stderr)]
    if len(lengths) < len(starts):  # a silence running to the end of the file
        lengths.append(_duration(wav) - starts[-1])
    return [(round(max(0.0, s), 2), round(d, 2)) for s, d in zip(starts, lengths, strict=False)]


def _duration(path: Path) -> float:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(r.stdout.strip() or 0.0)


def gag_offset(reel_dir: Path) -> float:
    """Where the reel starts in the MP4: after its cold open (reel_props.json gag.durationS, reelline/media/gags.py),
    0 without one. Every time in the props and captions.json is the reel's own."""
    path = reel_dir / "reel_props.json"
    if not path.is_file():
        return 0.0
    return float((json.loads(path.read_text(encoding="utf-8")).get("gag") or {}).get("durationS") or 0.0)
