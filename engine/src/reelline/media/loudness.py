"""Per-video-frame audio loudness for the talking bounce.

frame_loudness() returns one value per video frame, 0..1: the RMS of the samples
shown during that frame ([f / fps, (f + 1) / fps)), divided by the loudest frame's
RMS. Smoothing and how much the body squashes are decided in the renderer
(remotion/src/rig.ts).
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import numpy as np

DECODE_RATE = 16000


def rms_per_frame(samples: np.ndarray, rate: int, fps: int) -> np.ndarray:
    """RMS of each video frame's slice of `samples` (mono float), normalized to max 1."""
    n_frames = math.ceil(len(samples) * fps / rate)
    out = np.zeros(n_frames)
    for f in range(n_frames):
        chunk = samples[f * rate // fps : (f + 1) * rate // fps]
        if len(chunk):
            out[f] = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
    peak = out.max(initial=0.0)
    return out / peak if peak > 0 else out


def decode_mono(path: Path, rate: int = DECODE_RATE) -> np.ndarray:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH; needed to decode audio")
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to decode {path}:\n{result.stderr.decode(errors='replace')}")
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float64) / 32768.0


def frame_loudness(path: Path, fps: int = 30) -> list[float]:
    """Normalized loudness per video frame of the audio file at `path`."""
    return [round(float(v), 4) for v in rms_per_frame(decode_mono(path), DECODE_RATE, fps)]
