"""QA thresholds and prompts, and the Check, Report and Subject types the checks share."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from reelline.config import conf
from reelline.core.errors import CheckFailed
from reelline.paths import REMOTION_DIR

# The MP4's encoding (remotion/remotion.config.ts sets it from the same file).
ENCODE = json.loads((REMOTION_DIR / "encode.json").read_text(encoding="utf-8"))


# What ffprobe reports for Remotion's colorSpace "bt709" (zscale to limited range, tagged).
COLOR_TAGS = {
    "bt709": {"color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709", "color_range": "tv"}
}


QA = {
    # ---- technical (fail) ----
    # The end card comes in over the tag line and holds 1.5 s after the voice; Facebook Reels' 90 s
    # (PUBLISH) is the hard limit.
    "duration_s": (24.5, 62.5),
    "size": (1080, 1920),
    "fps": 30.0,
    "video_codec": "h264",
    # The publish guard's limit (fail): QA catches a reel publish would refuse.
    "max_mb": conf().publish.max_mb,
    # The video stream's format (warn otherwise) and minimum bitrate (warn below), from
    # remotion/encode.json.
    "video_format": {"pix_fmt": ENCODE["pixelFormat"], **COLOR_TAGS[ENCODE["colorSpace"]]},
    "min_video_mbps": ENCODE["qa"]["minVideoMbps"],
    # Integrated loudness of the MP4's audio (reelline/media/music.py MIX_LOUDNORM's target):
    # warn beyond warn_lu from it, fail beyond fail_lu.
    "loudness_lufs": -14.0,
    "loudness_warn_lu": 1.0,
    "loudness_fail_lu": 2.0,
    # True peak of the MP4's audio (dBTP): the mix is normalized to MIX_LOUDNORM TP -1.5 and
    # AAC adds a little; above warn: warn, above fail: it clips.
    "true_peak_warn_db": -1.0,
    "true_peak_fail_db": 0.0,
    # Silence in the voice-only track: below silence_db for longer than silence_max_s fails.
    "silence_max_s": 1.5,
    "silence_db": -45.0,
    "cover_size": (1080, 1920),
    # ---- end card ----
    # The card's hold after the voice (outro.json holdS) may be this much short (rounding)
    # before it fails.
    "card_voice_tolerance_s": 0.05,
    # ---- audio re-roll ----
    # Words below this mean letter score (scripts/render_reel.py LOW_SCORE, which
    # writes check_these.json) get their paragraph re-rolled, then a warning.
    "low_score": 0.3,
    "reroll_rounds": 1,
    # ---- opening (warn) ----
    # The voice's first sound (above silence_db) at most this far into the reel. An
    # opening emotion (a vocal tag before a yelled sentence) gets a limit for the first
    # word's start.
    "voice_start_max_s": 0.1,
    "emotional_first_word_max_s": 0.6,
    # The motion pass aims for a visual change every motion.json beats.maxGapS in the first
    # beats.windowS seconds; a longer stretch (when nothing fit) warns past this much more.
    "beat_gap_tolerance_s": 0.5,
    # ---- text ----
    # Leftover markup in displayed text: these substrings, and vocal tags (<...>).
    "markup": ("{{", "}}", "{", "}", "|", "@"),
    "vocal_tag": r"<[^<>\n]{1,40}>",
    "url_timeout_s": 10.0,
    "url_attempts": 2,
    # Caption field -> the steps.caption.limits platforms it goes to; the strictest applies.
    "caption_fields": {
        "post caption": ("instagram", "tiktok", "facebook", "youtube_description"),
        "youtube title": ("youtube_title",),
    },
    # ---- visual (Gemini vision, fail) ----
    # Where in the voice section (0 = first word, 1 = the outro's start) frames are
    # taken; each moves to the nearest time a caption is on screen, at least
    # caption_settle_s after its chunk popped in.
    "frames_at": (0.10, 0.35, 0.60, 0.85),
    "caption_settle_s": 0.3,
    # The model and its settings: the config's models.qa_vision (the pick's gemini-3.8-flash).
    # 2: the camera's 3/4 and back angles (channel docs/fx.md "Motion").
    "vision_prompt_version": 2,
    "vision_max_side_px": 1280,
    "vision_jpeg_quality": 90,
    "vision_cache": conf().channel.dir / "data" / "qa_vision_cache.json",
    # ---- outcome ----
    # Full re-renders after a failed QA (same script, images and TTS takes).
    "rerenders": 1,
}


FRAME_FAILS = tuple((c.key, c.fails_when, c.label) for c in conf().steps.qa.frame_checks)
QA_SCHEMA = {
    "type": "object",
    "properties": {**{key: {"type": "boolean"} for key, _, _ in FRAME_FAILS}, "notes": {"type": "string"}},
    "required": [*(key for key, _, _ in FRAME_FAILS), "notes"],
}
QA_PROMPT = conf().steps.qa.frame_prompt.read_text(encoding="utf-8")


class QaError(CheckFailed):
    """QA couldn't run (a missing reel dir, a failed vision call, ...)."""


@dataclass
class Check:
    group: str  # technical | audio | opening | motion | end card | versions | fx | text | visual
    name: str
    result: str  # pass | warn | fail | skip
    value: Any = None
    threshold: Any = None
    lines: list[str] = field(default_factory=list)  # review lines when not passed


@dataclass
class Report:
    checks: list[Check]
    calls: list[dict] = field(default_factory=list)  # paid vision calls (reelline/media/images/vision.py call_row())

    @property
    def status(self) -> str:
        return "fail" if any(c.result == "fail" for c in self.checks) else "pass"

    @property
    def failures(self) -> list[str]:
        return _dedup(f"{c.group}: {line}" for c in self.checks if c.result == "fail" for line in c.lines)

    @property
    def warnings(self) -> list[str]:
        return _dedup(line for c in self.checks if c.result == "warn" for line in c.lines)

    @property
    def cost_usd(self) -> float:
        return sum(c["cost_usd"] for c in self.calls)

    def as_json(self) -> dict:
        return {
            "status": self.status,
            "failures": self.failures,
            "warnings": self.warnings,
            "checks": [asdict(c) for c in self.checks],
            "vision_cost_usd": round(self.cost_usd, 6),
        }


def _dedup(lines) -> list[str]:
    return list(dict.fromkeys(lines))


@dataclass
class Subject:
    """What the checks look at."""

    reel_dir: Path
    mp4: Path
    cover: Path | None
    voice_wav: Path | None
    check_these: list[dict]
    # True: this render wrote the cover, so its safe-area check passed; None: decide
    # from the files (cover.png newer than cover_props.json).
    cover_fresh: bool | None = None
    caption: str | None = None  # None: the caption checks are skipped
    youtube_title: str | None = None
    hashtags: list[str] | None = None
    source_url: str | None = None
    reel_id: int | None = None  # the credits line's id (None: the "…" preview form)


def subject_from_dir(reel_dir: Path, **caption) -> Subject:
    """A Subject for an existing render in out/reels/<name>/."""
    check = reel_dir / "check_these.json"
    cover = reel_dir / "cover.png"
    voice = reel_dir.parent / f"{reel_dir.name}.wav"
    return Subject(
        reel_dir,
        reel_dir / "reel.mp4",
        cover if cover.is_file() else None,
        voice if voice.is_file() else None,
        json.loads(check.read_text(encoding="utf-8")) if check.is_file() else [],
        **caption,
    )
