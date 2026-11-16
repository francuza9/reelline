"""End card, per-platform version and fact graphic checks."""

from __future__ import annotations

import json
from pathlib import Path

from reelline.pipeline import qa as _qa
from reelline.pipeline.qa.base import QA, Check, Subject
from reelline.pipeline.qa.media import _duration
from reelline.pipeline.qa.text import caption_chunks


def outro_props(reel_dir: Path) -> dict | None:
    """reel_props.json's outro (the end card's window and facts), None without one."""
    path = reel_dir / "reel_props.json"
    return json.loads(path.read_text(encoding="utf-8")).get("outro") if path.is_file() else None


def card_checks(s: Subject) -> list[Check]:
    """The end card's facts and its hold after the voice (module docstring); none without an outro."""
    from reelline.media.outro import card_problems, load_outro

    outro = outro_props(s.reel_dir)
    if not outro:
        return []
    facts = outro.get("facts") or []
    lines = {f["field"]: f["text"] for f in facts}
    problems = card_problems(lines, load_outro())
    if problems:
        out = [Check("end card", "facts", "fail", lines, "outro.json fields", [f"end card: {p}" for p in problems])]
    elif not facts:
        details = bool(outro.get("details"))
        out = [
            Check(
                "end card",
                "facts",
                "fail" if details else "warn",
                {},
                "at least one fact",
                [
                    "the end card has no facts, but the script points to it (the details line)"
                    if details
                    else "the end card has no facts (no <script>.endcard.json): the footer only"
                ],
            )
        ]
    else:
        out = [Check("end card", "facts", "pass", lines, "outro.json fields")]
    if s.voice_wav is not None and s.voice_wav.is_file():
        end, want = round(_duration(s.voice_wav), 3), load_outro().hold_s
        hold = round(float(outro["end"]) - end, 3)
        short = hold < want - QA["card_voice_tolerance_s"]
        out.append(
            Check(
                "end card",
                "hold",
                "fail" if short else "pass",
                hold,
                want,
                [f"the voice runs to {end:.2f}s, leaving the end card {hold:.2f}s of music only, not {want:g}s"]
                if short
                else [],
            )
        )
    return out


def version_checks(s: Subject) -> list[Check]:
    """Each platform's file next to reel.mp4 (module docstring); none without reel.mp4."""
    from reelline.media.share import PLATFORMS, platform_file

    if not s.mp4.is_file():
        return []
    if not (s.reel_dir / "versions.json").is_file():
        return [
            Check(
                "versions",
                "files",
                "warn",
                None,
                list(PLATFORMS),
                ["no platform versions (rendered before them): every platform gets reel.mp4"],
            )
        ]
    fmt = QA["video_format"]
    checks = []
    measured: dict[tuple[int, int], tuple[dict, dict | None]] = {}  # one probe per file (versions may be links)

    def measure(f: Path) -> tuple[dict, dict | None]:
        key = (f.stat().st_dev, f.stat().st_ino)
        if key not in measured:
            info = _qa.probe(f)
            measured[key] = (info, _qa.audio_levels(f) if info["audio"] else None)
        return measured[key]

    main, main_levels = measure(s.mp4)
    for platform in PLATFORMS:
        f = platform_file(s.reel_dir, platform)
        if not f.is_file():
            checks.append(Check("versions", platform, "fail", None, None, [f"{f.name} is missing"]))
            continue
        (info, levels), problems, warn = measure(f), [], []
        if abs(info["duration"] - main["duration"]) > 1 / QA["fps"] + 1e-3:
            problems.append(f"{info['duration']:.2f}s, reel.mp4 is {main['duration']:.2f}s")
        v, w, h = info["video"], *QA["size"]
        if v is None:
            problems.append("no video stream")
        else:
            problems += [
                f"{k} {v[k]} (want {want})"
                for k, want in (("width", w), ("height", h), ("codec", QA["video_codec"]))
                if v[k] != want
            ]
            if abs(v["fps"] - QA["fps"]) > 0.01:
                problems.append(f"{v['fps']:g} fps (want {QA['fps']:g})")
            problems += [f"{k} {v.get(k)} (want {fmt[k]})" for k in fmt if v.get(k) != fmt[k]]
        value = {"duration": round(info["duration"], 2)}
        if not info["audio"]:
            problems.append("no audio stream")
        else:
            value.update(lufs=levels["lufs"], tp=levels["tp"])
        same = (
            main_levels is not None
            and levels is not None
            and abs(levels["lufs"] - main_levels["lufs"]) <= 0.1
            and abs(levels["tp"] - main_levels["tp"]) <= 0.1
        )
        if levels is not None and not same:
            off = abs(levels["lufs"] - QA["loudness_lufs"])
            line = f"loudness {levels['lufs']:.1f} LUFS ({levels['lufs'] - QA['loudness_lufs']:+.1f} LU)"
            if off > QA["loudness_fail_lu"]:
                problems.append(line)
            elif off > QA["loudness_warn_lu"]:
                warn.append(line)
            if levels["tp"] > QA["true_peak_fail_db"]:
                problems.append(f"true peak {levels['tp']:.1f} dBTP: it clips")
        result = "fail" if problems else "warn" if warn else "pass"
        checks.append(
            Check(
                "versions",
                platform,
                result,
                value,
                "as reel.mp4, technical limits",
                [f"{f.name}: {x}" for x in problems + warn],
            )
        )
    return checks


def fx_checks(s: Subject) -> list[Check]:
    """The fact graphics' place on screen and in time (module docstring); none without any."""
    from reelline.media.fx import back_s, card_bounds, eye_tops, safe_problems, show
    from reelline.media.fx import config as fx_config

    path = s.reel_dir / "reel_props.json"
    props = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    events = props.get("fx") or []
    if not events:
        return []
    used = [
        f"¶{e.get('paragraph', '?')} {e['data']['template']} „{show(e['data'])}“ {e['start']:.1f}-{e['end']:.1f}s"
        for e in events
    ]
    problems = safe_problems()
    eyes = eye_tops(props["anchors"])
    if card_bounds()["bottom"] > eyes:
        problems.append(f"the card's bottom {card_bounds()['bottom']:.0f} is below the mascot's eyes' top {eyes:.0f}")
    for c in caption_chunks(s.reel_dir) or []:
        if len(c["lines"]) > 1 and any(c["chunk_start"] < e["end"] and c["chunk_end"] > e["start"] for e in events):
            problems.append(
                f"a {len(c['lines'])}-line caption at {c['chunk_start']:.2f}-{c['chunk_end']:.2f}s "
                "while a card is out (one line only)"
            )
    for a, b in zip(events, events[1:], strict=False):
        if b["start"] < a["end"]:
            problems.append(f"cards at {a['start']:.2f}s and {b['start']:.2f}s overlap")
    outro = props.get("outro")
    problems += [
        f"the card at {e['start']:.2f}s is still out at the end card ({outro['start']:.2f}s)"
        for e in events
        if outro and e["end"] > outro["start"] + 1e-6
    ]
    opening = fx_config()["opening"]
    hold = float(opening["minHoldS"])
    for e in events:
        what = f"the {e['data']['template']} card on paragraph {e.get('paragraph', '?')}"
        if e.get("paragraph") == 1 and opening["fromFrame0"] and not (e.get("frame0") and e["start"] == 0):
            problems.append(f"{what} isn't out from frame 0")
        elif e.get("frame0") and e.get("paragraph") != 1:
            problems.append(f"{what} is out from frame 0 (only paragraph 1's is)")
        elif e.get("frame0") and e["end"] - back_s() - e["start"] < hold - 1e-6:
            problems.append(f"{what} (frame 0) holds {e['end'] - back_s() - e['start']:.2f}s, under {hold:g}s")
    return [
        Check(
            "fx",
            "fact graphics",
            "fail" if problems else "pass",
            used,
            "safe area, eyes, one-line captions",
            [f"fx: {p}" for p in problems],
        )
    ]
