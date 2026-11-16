"""Shared pytest fixtures and their helpers (registered by conftest.py)."""

from __future__ import annotations

import json
import math
import os
import stat
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image
from reelline.media import style
from reelline.media.share import link_versions

BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def make_video(path: Path, size=(1080, 1920), seconds=2.0, audio=True, codec="libx264", fps=30, fmt=None):
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x2a2350:s={size[0]}x{size[1]}:r={fps}:d={seconds}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", codec, *(fmt or ["-pix_fmt", "yuv420p", *BT709])] + (
        ["-preset", "ultrafast"] if codec == "libx264" else []
    )
    cmd += (["-c:a", "aac", "-shortest"] if audio else []) + [str(path)]
    subprocess.run(cmd, check=True)
    return path


def make_voice(path: Path, segments: list[tuple[str, float]], rate=24000):
    """("tone" | "silence", seconds) segments as a mono 16-bit WAV."""
    frames = bytearray()
    for kind, seconds in segments:
        for i in range(round(seconds * rate)):
            v = int(12000 * math.sin(2 * math.pi * 220 * i / rate)) if kind == "tone" else 0
            frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


CHUNKS = [
    {"chunk_start": 0.0, "chunk_end": 0.4, "lines": [[{"text": "სტუდენტებს", "start": 0.0, "end": 0.4}]]},
    {
        "chunk_start": 0.4,
        "chunk_end": 0.8,
        "lines": [[{"text": "ახალი", "start": 0.4, "end": 0.6}, {"text": "კონკურსი", "start": 0.6, "end": 0.8}]],
    },
    {"chunk_start": 0.8, "chunk_end": 1.2, "lines": [[{"text": "ელოდებათ.", "start": 0.8, "end": 1.2}]]},
    # The tag paragraph, as render_reel.py chunks it: its own chunk, TAG_LINE exactly.
    {
        "chunk_start": 1.2,
        "chunk_end": 1.8,
        "lines": [
            [{"text": "კაი,", "start": 1.2, "end": 1.35}],
            [{"text": "თავისუფალი", "start": 1.4, "end": 1.65}, {"text": "ხარ.", "start": 1.65, "end": 1.75}],
        ],
    },
]


FACTS = [{"field": "name", "text": "GITA-ს კონკურსი"}, {"field": "deadline", "text": "29 ოქტომბრამდე"}]


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("v") / "reel.mp4")


@pytest.fixture
def reel(tmp_path, video):
    """A reel dir like render_reel.py writes: reel.mp4, cover, captions, props, voice track."""
    d = tmp_path / "reels" / "demo"
    d.mkdir(parents=True)
    (d / "reel.mp4").write_bytes(video.read_bytes())
    (d / "cover_props.json").write_text("{}")
    Image.new("RGB", (1080, 1920)).save(d / "cover.png")
    (d / "captions.json").write_text(json.dumps(CHUNKS, ensure_ascii=False), encoding="utf-8")
    (d / "reel_props.json").write_text(
        json.dumps({"outro": {"start": 1.9, "end": 3.4, "facts": FACTS, "details": True}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (d / "check_these.json").write_text("[]")
    make_voice(tmp_path / "reels" / "demo.wav", [("tone", 0.8), ("silence", 0.3), ("tone", 0.8)])
    # Its four platform versions (reelline/media/share.py): no share line here, so each is reel.mp4.
    link_versions(d, d / "reel.mp4")
    (d / "versions.json").write_text(json.dumps({"arrow": False, "files": {}}), encoding="utf-8")
    return d


@pytest.fixture
def fake_npx(tmp_path, monkeypatch):
    """fake_npx(body): an `npx` first on PATH running the bash `body`."""

    def make(body: str) -> None:
        npx = tmp_path / "npx"
        npx.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
        npx.chmod(npx.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return make


@pytest.fixture
def straight(monkeypatch):
    """The straight style for this test (conftest's classic_style pins classic for the rest)."""
    monkeypatch.setattr(style, "STYLE_MODE", "straight")
