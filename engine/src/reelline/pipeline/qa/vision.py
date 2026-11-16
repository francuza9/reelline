"""Visual checks: frames through Gemini vision, cached by frame hash, and the Checker that runs every check."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import httpx

from reelline.config import conf
from reelline.pipeline import qa as _qa
from reelline.pipeline.qa.base import FRAME_FAILS, QA, QA_PROMPT, QA_SCHEMA, Check, QaError, Report, Subject
from reelline.pipeline.qa.graphics import card_checks, fx_checks, version_checks
from reelline.pipeline.qa.media import _run, gag_offset
from reelline.pipeline.qa.motion import motion_checks
from reelline.pipeline.qa.opening import audio_checks, opening_checks
from reelline.pipeline.qa.technical import technical_checks
from reelline.pipeline.qa.text import text_checks


def voice_end(reel_dir: Path, chunks: list[dict]) -> float:
    """Where the voice section ends: the outro's start, else the last caption's end."""
    props = reel_dir / "reel_props.json"
    if props.is_file():
        outro = json.loads(props.read_text(encoding="utf-8")).get("outro")
        if outro:
            return float(outro["start"])
    return float(chunks[-1]["chunk_end"])


def frame_times(chunks: list[dict], end: float) -> list[float]:
    """QA["frames_at"] of [0, end], each moved into the nearest caption chunk, at least
    QA["caption_settle_s"] after it popped in (its middle, if it's shorter)."""
    settle = QA["caption_settle_s"]
    out = []
    for frac in QA["frames_at"]:
        target = frac * end

        def distance(c, target=target):
            return (
                0.0
                if c["chunk_start"] <= target < c["chunk_end"]
                else min(abs(target - c["chunk_start"]), abs(target - c["chunk_end"]))
            )

        c = min(chunks, key=distance)
        lo, hi = c["chunk_start"] + settle, c["chunk_end"] - 1 / QA["fps"]
        out.append(round(min(max(target, lo), hi) if lo < hi else (c["chunk_start"] + c["chunk_end"]) / 2, 3))
    return out


def extract_frame(mp4: Path, t: float, out: Path) -> bytes:
    r = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", str(out)])
    if r.returncode != 0 or not out.is_file():
        raise QaError(f"frame extraction at {t:.2f}s failed: {r.stderr.strip()[-300:]}")
    return out.read_bytes()


def vision_jpeg(png: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        rgb = im.convert("RGB")
    rgb.thumbnail((QA["vision_max_side_px"], QA["vision_max_side_px"]))
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=QA["vision_jpeg_quality"])
    return buf.getvalue()


class VisionCache:
    """Frame verdicts by (model, prompt version, frame sha256), in one JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    @staticmethod
    def key(frame_sha: str) -> str:
        return f"{conf().models.qa_vision.model}:{QA['vision_prompt_version']}:{frame_sha}"

    def get(self, frame_sha: str) -> dict | None:
        return self.data.get(self.key(frame_sha))

    def put(self, frame_sha: str, result: dict) -> None:
        self.data[self.key(frame_sha)] = result
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def ask_vision(client, jpeg: bytes) -> tuple[dict, dict]:
    """(verdict, call row) for one frame (the qa_vision role; `client`: its provider or connection)."""
    from reelline.providers import CostLedger, LLMError, get_vision

    rows: list[dict] = []
    try:
        reply = get_vision("qa_vision", client).look(
            jpeg,
            QA_PROMPT,
            QA_SCHEMA,
            purpose="qa_vision",
            prompt_version=QA["vision_prompt_version"],
            ledger=CostLedger(rows=rows),
        )
    except LLMError as exc:
        raise QaError(f"QA vision call failed: {exc}") from exc
    result = reply.data
    missing = [k for k in QA_SCHEMA["required"] if k not in result]
    if missing:
        raise QaError(f"QA vision result lacks {', '.join(missing)}: {result}")
    return result, rows[0]


def visual_checks(
    s: Subject, client_factory: Callable[[], object], cache: VisionCache
) -> tuple[list[Check], list[dict]]:
    path = s.reel_dir / "captions.json"
    if not s.mp4.is_file() or not path.is_file():
        return [Check("visual", "frames", "fail", None, None, ["no reel or captions.json to sample frames from"])], []
    chunks = json.loads(path.read_text(encoding="utf-8"))
    frames_dir = s.reel_dir / "qa_frames"
    frames_dir.mkdir(exist_ok=True)
    checks, calls, client = [], [], None
    offset = gag_offset(s.reel_dir)  # the reel's times, in an MP4 that opens on a cold open
    for n, t in enumerate(frame_times(chunks, voice_end(s.reel_dir, chunks)), 1):
        png = _qa.extract_frame(s.mp4, t + offset, frames_dir / f"frame{n}.png")
        sha = hashlib.sha256(png).hexdigest()
        result, cached = cache.get(sha), True
        if result is None:
            client = client or client_factory()
            result, row = ask_vision(client, _qa.vision_jpeg(png))
            cached = False
            calls.append(row)
            cache.put(sha, result)
        bad = [label for key, failing, label in FRAME_FAILS if result[key] == failing]
        notes = f" ({result['notes']})" if result.get("notes") and bad else ""
        checks.append(
            Check(
                "visual",
                f"frame {n}",
                "fail" if bad else "pass",
                {"t": t, "sha256": sha[:16], "cached": cached, **result},
                {k: not failing for k, failing, _ in FRAME_FAILS},
                [f"frame {n} at {t:.1f}s: {', '.join(bad)}{notes}"] if bad else [],
            )
        )
    return checks, calls


def gemini_client():
    from reelline.providers import get_vision

    return get_vision("qa_vision")


class Checker:
    """Runs every check on a Subject; Gemini and HTTP are injectable for tests."""

    def __init__(
        self,
        client_factory: Callable[[], object] | None = None,
        http: httpx.Client | None = None,
        cache_path: Path | None = None,
    ):
        self.client_factory = client_factory or (lambda: _qa.gemini_client())
        self.http = http
        self.cache = VisionCache(cache_path or QA["vision_cache"])

    def run(self, s: Subject) -> Report:
        from reelline.pipeline.fetch import USER_AGENT

        http = self.http or httpx.Client(headers={"User-Agent": USER_AGENT})
        try:
            checks = (
                technical_checks(s)
                + audio_checks(s)
                + opening_checks(s)
                + motion_checks(s)
                + card_checks(s)
                + version_checks(s)
                + fx_checks(s)
                + text_checks(s, http)
            )
        finally:
            if self.http is None:
                http.close()
        visual, calls = visual_checks(s, self.client_factory, self.cache)
        return Report(checks + visual, calls)
