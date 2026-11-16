"""Audio and opening checks: low-score words, the first sound, the opening emotion and the beat gaps."""

from __future__ import annotations

import json
from pathlib import Path

from reelline.pipeline import qa as _qa
from reelline.pipeline.qa.base import QA, Check, Subject
from reelline.pipeline.qa.text import caption_words


def low_words(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["score"] < QA["low_score"]]


def word_line(r: dict) -> str:
    return f"check: ¶{r['paragraph']} {r['start']:.1f}s {r['word']} ({r['score']:.2f})"


def audio_checks(s: Subject) -> list[Check]:
    rows = low_words(s.check_these)
    return [
        Check(
            "audio", "low-score words", "warn" if rows else "pass", rows, QA["low_score"], [word_line(r) for r in rows]
        )
    ]


def voice_onset(wav: Path) -> float:
    """Seconds of silence (below QA["silence_db"]) before the voice-only track's first sound."""
    lead = _qa.silences(wav, QA["silence_db"], 0.01)
    return lead[0][1] if lead and lead[0][0] == 0 else 0.0


def opening_emotion(reel_dir: Path) -> str | None:
    """The reel's opening emotion (reel_props.json opening.emotion), None without one."""
    path = reel_dir / "reel_props.json"
    if not path.is_file():
        return None
    return (json.loads(path.read_text(encoding="utf-8")).get("opening") or {}).get("emotion")


def motion_beats(props: dict) -> list[dict]:
    """The reel's visual changes from its props (reelline/media/motion.py beats())."""
    from reelline.media.motion import beats

    motion = props.get("motion") or {}
    return beats(
        props.get("screenImages") or [],
        props.get("fx") or [],
        motion.get("zooms") or [],
        motion.get("reactions") or [],
        motion.get("glances") or [],
        motion.get("shots") or [],
        motion.get("leans") or [],
        motion.get("stalks") or [],
        motion.get("bounces") or [],
    )


def beat_check(reel_dir: Path) -> Check | None:
    """The opening's longest stretch without a visual change (module docstring); None for
    props without a motion pass."""
    from reelline.media.motion import config as motion_config
    from reelline.media.motion import largest_gap

    path = reel_dir / "reel_props.json"
    props = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    motion = props.get("motion")
    if not motion:
        return None
    cfg = motion_config()["beats"]
    window, limit = float(cfg["windowS"]), float(cfg["maxGapS"]) + QA["beat_gap_tolerance_s"]
    found = motion_beats(props)
    gap, at = largest_gap(found, window)
    return Check(
        "opening",
        "beats",
        "warn" if gap > limit else "pass",
        round(gap, 2),
        limit,
        [f"{gap:.1f}s without a visual change from {at:.1f}s (first {window:g}s; max {limit:g}s)"]
        if gap > limit
        else [],
    )


def opening_checks(s: Subject) -> list[Check]:
    out = []
    emotion = opening_emotion(s.reel_dir)
    if s.voice_wav is not None and s.voice_wav.is_file():
        onset = voice_onset(s.voice_wav)
        late = onset > QA["voice_start_max_s"]
        out.append(
            Check(
                "opening",
                "voice start",
                "warn" if late else "pass",
                onset,
                QA["voice_start_max_s"],
                [f"the voice starts {onset:.2f}s in (max {QA['voice_start_max_s']:g}s)"] if late else [],
            )
        )
    start = next((w["start"] for w in caption_words(s.reel_dir) or [] if w.get("start") is not None), None)
    if start is not None and emotion:
        late = start > QA["emotional_first_word_max_s"]
        out.append(
            Check(
                "opening",
                "first word",
                "warn" if late else "pass",
                start,
                QA["emotional_first_word_max_s"],
                [f"first word at {start:.2f}s ({emotion} opening; max {QA['emotional_first_word_max_s']:g}s)"]
                if late
                else [],
            )
        )
    beat = beat_check(s.reel_dir)
    if beat is not None:
        out.append(beat)
    return out
