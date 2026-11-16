"""Camera checks: every shot inside the framing limits, and the cuts."""

from __future__ import annotations

import json

from reelline.pipeline.qa.base import QA, Check, Subject
from reelline.pipeline.qa.opening import motion_beats
from reelline.pipeline.qa.text import caption_chunks, caption_words


def framing_problems(props: dict, words: list[dict] | None) -> list[str]:
    """The camera's shots against the module docstring's rules (motion group)."""
    from reelline.media.angles import FRONT
    from reelline.media.angles import names as angle_names
    from reelline.media.motion import config as motion_config
    from reelline.media.motion import framing_problems as shot_problems

    fc = motion_config()["framing"]
    motion = props.get("motion") or {}
    shots = motion.get("shots") or []
    specs = fc["shots"]
    problems = []
    unknown = sorted({s["shot"] for s in shots} - specs.keys())
    if unknown:
        return [f"unknown shot(s) {', '.join(unknown)} (motion.json framing.shots)"]
    unknown = sorted({s["angle"] for s in shots if s.get("angle")} - set(angle_names()))
    if unknown:
        return [f"unknown camera angle(s) {', '.join(unknown)} (remotion/src/angles.json rigs)"]
    if shots[0]["start"] != 0:
        problems.append(f"the first shot starts at {shots[0]['start']:.2f}s, not 0")
    for name, angle in dict.fromkeys((s["shot"], s.get("angle") or FRONT) for s in shots):
        problems += shot_problems(name, specs[name], props["anchors"], props["screen"], angle)
    ends = [s["start"] for s in shots[1:]] + [float("inf")]
    pushes = [(z["start"], z["end"]) for z in motion.get("zooms") or []]
    cards = [(e["start"], e["end"]) for e in props.get("fx") or []]
    share = props.get("share")
    ac = fc["angles"]

    def label(s: dict) -> str:
        return s["shot"] + (f" ({s['angle']})" if s.get("angle") else "")

    for prev, (s, end) in zip([None, *shots], zip(shots, ends, strict=False), strict=False):
        angle = s.get("angle") or FRONT
        if prev and ((s["shot"], angle) == (prev["shot"], prev.get("angle") or FRONT) or s["start"] <= prev["start"]):
            problems.append(f"the {label(s)} shot at {s['start']:.2f}s follows {label(prev)} at {prev['start']:.2f}s")
        spec = specs[s["shot"]]
        card = next((c for c in cards if c[0] < end and c[1] > s["start"]), None)
        if card and (spec["close"] or s["shot"] not in fc["cardShots"] or angle not in ac["cardAngles"]):
            problems.append(f"the {label(s)} shot at {s['start']:.2f}s runs while the card at {card[0]:.2f}s is out")
        if share and share["start"] < end and share["end"] > s["start"] and angle not in ac["arrowAngles"]:
            problems.append(
                f"the {label(s)} shot at {s['start']:.2f}s runs while the share arrow is up "
                f"({share['start']:.2f}-{share['end']:.2f}s)"
            )
        push = next((z for z in pushes if z[0] < end and z[1] > s["start"]), None)
        if push and spec["close"]:
            problems.append(f"the {label(s)} shot at {s['start']:.2f}s runs during the push-in at {push[0]:.2f}s")
    for s in shots[1:]:
        word = next((w for w in words or [] if w.get("start") is not None and w["start"] < s["start"] < w["end"]), None)
        if word:
            problems.append(
                f"the cut at {s['start']:.2f}s is inside the word „{word['text']}“ "
                f"({word['start']:.2f}-{word['end']:.2f}s)"
            )
    return problems


def motion_checks(s: Subject) -> list[Check]:
    """The camera's shots (fail) and the whole reel's beats (warn); none for props without shots."""
    from reelline.media.motion import config as motion_config
    from reelline.media.motion import largest_gap

    path = s.reel_dir / "reel_props.json"
    props = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    shots = (props.get("motion") or {}).get("shots")
    if not shots:
        return []
    problems = framing_problems(props, caption_words(s.reel_dir))
    out = [
        Check(
            "motion",
            "framing",
            "fail" if problems else "pass",
            " ".join(f"{x['start']:.1f} {x['shot']}" + (f" ({x['angle']})" if x.get("angle") else "") for x in shots),
            "framing.safe, alternating, no close shot over a card or a push-in, front over a card and the "
            "share arrow, cuts between words",
            [f"framing: {p}" for p in problems],
        )
    ]
    cfg = motion_config()["beats"]
    limit = float(cfg["body"]["maxGapS"]) + QA["beat_gap_tolerance_s"]
    outro = props.get("outro")
    end = outro["start"] if outro else max([c["chunk_end"] for c in caption_chunks(s.reel_dir) or []], default=0.0)
    gap, at = largest_gap(motion_beats(props), end)
    out.append(
        Check(
            "motion",
            "beats",
            "warn" if gap > limit else "pass",
            round(gap, 2),
            limit,
            [f"{gap:.1f}s without a visual change from {at:.1f}s (whole reel; max {limit:g}s)"] if gap > limit else [],
        )
    )
    return out
