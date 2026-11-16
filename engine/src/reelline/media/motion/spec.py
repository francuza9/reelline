"""motion.json read through the style's overrides and checked."""

from __future__ import annotations

import json
from functools import lru_cache

from reelline.media import angles as angle_art
from reelline.media import motion as _m
from reelline.media import style as reel_style
from reelline.media.motion.base import MotionError


def config() -> dict:
    """motion.json with the style's overrides (reelline/media/style.py Style.motion: straight's closing jab
    and share line get no punchline moves), checked."""
    return _config(reel_style.current().name)


@lru_cache(maxsize=4)
def _config(style_name: str) -> dict:
    try:
        spec = reel_style.over(
            json.loads(_m.MOTION_JSON.read_text(encoding="utf-8")), reel_style.STYLES[style_name].motion
        )
        for section, keys in (
            ("punchline", ("maxPerReel", "minGapS", "notBeforeS")),
            ("zoom", ("inS", "holdS", "outS")),
            ("reaction", ("maxPerReel", "minGapS", "delayS", "inS", "outS")),
            ("tvGlance", ("delayS", "dartS", "holdS", "returnS", "minGapS", "notBeforeS")),
            ("beats", ("windowS", "maxGapS", "minSpacingS", "notBeforeS", "snapS", "sentenceEndBonusS")),
            ("bounce", ("leadS", "amount", "widthRatio", "tauS", "hz", "durationS", "minGapS", "notBeforeS")),
            (
                "lean",
                (
                    "maxPerReel",
                    "minGapS",
                    "notBeforeS",
                    "inS",
                    "outS",
                    "overshoot",
                    "scaleX",
                    "scaleY",
                    "stalkStretch",
                    "eyeGrow",
                ),
            ),
            ("stalks", ("delayS", "inS", "overshoot", "outS", "maxShiftS", "minGapS")),
            (
                "framing",
                ("minShotS", "maxShotS", "notBeforeS", "cutLeadS", "pauseS", "sentenceBonusS", "flightPadS", "tvReadS"),
            ),
        ):
            for key in keys:
                float(spec[section][key])
        lo, hi = spec["zoom"]["scale"]
        rlo, rhi = spec["reaction"]["lengthS"]
        blo, bhi = spec["beats"]["zoomScale"]
        hlo, hhi = spec["lean"]["holdS"]
        slo, shi = spec["stalks"]["lengthS"]
        flo, fhi = spec["framing"]["lengthS"]
        kinds = list(spec["beats"]["kinds"])
        for flag in (
            spec["beats"]["enabled"],
            spec["beats"]["body"]["enabled"],
            spec["bounce"]["enabled"],
            spec["lean"]["enabled"],
            spec["stalks"]["enabled"],
            spec["framing"]["enabled"],
        ):
            bool(flag)
        float(spec["beats"]["body"]["maxGapS"])
        poses, by_tag = spec["reaction"]["poses"], spec["reaction"]["byTag"]
        stalk_poses = spec["stalks"]["poses"]
        stalk_names = [pose for _, pose in spec["stalks"]["byStyle"]] + [
            p for p in spec["stalks"]["byMood"].values() if p
        ]
        for pose in stalk_poses.values():
            for key in ("stretch", "eyeGrow", "pupilScale", "lid", "leanDeg", "cross"):
                float(pose[key])
            float(pose["pupil"][0]), float(pose["pupil"][1])
        shots, fc = spec["framing"]["shots"], spec["framing"]
        for shot in shots.values():
            float(shot["scale"]), float(shot["origin"][0]), float(shot["origin"][1]), bool(shot["close"])
        for key in ("captionClearancePx", "bottomPx", "sideMarginPx", "rigPadPx"):
            float(fc["safe"][key])
        weights = {name: float(w) for name, w in fc["weights"].items()}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, IndexError) as exc:
        raise MotionError(f"Can't read {_m.MOTION_JSON}: {exc}") from exc
    if not 1 < lo <= hi or not 0 < rlo <= rhi or not 1 < blo <= bhi:
        raise MotionError(
            f"{_m.MOTION_JSON.name}: zoom.scale and beats.zoomScale must be > 1 and reaction.lengthS > 0, each [low, high]"
        )
    if not 0 < hlo <= hhi or not 0 < slo <= shi or not 0 < flo <= fhi:
        raise MotionError(
            f"{_m.MOTION_JSON.name}: lean.holdS, stalks.lengthS and framing.lengthS must be > 0, each [low, high]"
        )
    if not kinds or set(kinds) - {"zoom", "glance"}:
        raise MotionError(f"{_m.MOTION_JSON.name}: beats.kinds must list zoom and/or glance, got {kinds}")
    if not 0 < spec["beats"]["minSpacingS"] < spec["beats"]["maxGapS"] <= spec["beats"]["body"]["maxGapS"]:
        raise MotionError(
            f"{_m.MOTION_JSON.name}: beats.minSpacingS must be > 0 and under beats.maxGapS, itself at most "
            "beats.body.maxGapS"
        )
    missing = sorted({*spec["punchline"]["tags"], "jab"} - by_tag.keys())
    unknown = sorted(set(by_tag.values()) - poses.keys())
    if missing or unknown:
        raise MotionError(
            f"{_m.MOTION_JSON.name}: reaction.byTag "
            + (f"has no pose for {', '.join(missing)}" if missing else f"names unknown poses {', '.join(unknown)}")
        )
    unknown = sorted(set(stalk_names) - stalk_poses.keys())
    if unknown:
        raise MotionError(f"{_m.MOTION_JSON.name}: stalks.byStyle / byMood name unknown poses {', '.join(unknown)}")
    if fc["first"] not in shots or shots[fc["first"]]["close"] or len(shots) < 2:
        raise MotionError(
            f"{_m.MOTION_JSON.name}: framing needs at least two shots and a `first` one that isn't close, "
            f"got first {fc['first']!r} of {', '.join(shots)}"
        )
    if (
        set(fc["cardShots"]) - shots.keys()
        or any(shots[n]["close"] for n in fc["cardShots"])
        or not fc["cardShots"]
        or set(weights) != set(shots)
        or min(weights.values()) <= 0
    ):
        raise MotionError(
            f"{_m.MOTION_JSON.name}: framing.cardShots must name shots that aren't close, and "
            "framing.weights give every shot a weight > 0"
        )
    if not fc["arrowShots"] or set(fc["arrowShots"]) - shots.keys():
        raise MotionError(f"{_m.MOTION_JSON.name}: framing.arrowShots must name at least one of {', '.join(shots)}")
    if not 0 < fc["minShotS"] <= flo <= fhi <= fc["maxShotS"] or any(s["scale"] < 1 for s in shots.values()):
        raise MotionError(
            f"{_m.MOTION_JSON.name}: framing needs 0 < minShotS <= lengthS <= maxShotS and every shot's scale at least 1"
        )
    _check_angles(fc)
    return spec


config.cache_clear = _config.cache_clear  # type: ignore[attr-defined]


ANGLE_KINDS = ("share", "beat")


def _check_angles(fc: dict) -> None:
    """framing.angles against remotion/src/angles.json's angles (module docstring)."""
    try:
        known = angle_art.names()
    except angle_art.AngleError as exc:
        raise MotionError(str(exc)) from None
    try:
        ac = fc["angles"]
        bool(ac["enabled"]), float(ac["notBeforeS"]), float(ac["minFrontS"]), int(ac["attempts"])
        lists = {key: list(ac[key]) for key in ("cardAngles", "arrowAngles")}
        rules = ac["rules"]
        for rule in rules.values():
            float(rule["lengthS"][0]), float(rule["lengthS"][1]), list(rule["shots"]), bool(rule["face"])
            if rule["kind"] == "share":
                float(rule["share"][0]), float(rule["share"][1])
            elif rule["kind"] == "beat":
                int(rule["maxPerReel"]), float(rule["pictureS"][0]), float(rule["pictureS"][1]), rule["elsewhere"]
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise MotionError(f"{_m.MOTION_JSON.name}: framing.angles: {exc!r}") from exc
    unknown = sorted({a for names in lists.values() for a in names} - set(known))
    if unknown or any("front" not in names for names in lists.values()):
        raise MotionError(
            f"{_m.MOTION_JSON.name}: framing.angles.cardAngles and arrowAngles must name known angles "
            f"({', '.join(known)}), front among them" + (f"; unknown: {', '.join(unknown)}" if unknown else "")
        )
    unknown = sorted(set(rules) - set(known) | ({"front"} & set(rules)))
    if unknown:
        raise MotionError(
            f"{_m.MOTION_JSON.name}: framing.angles.rules names unknown angle(s) {', '.join(unknown)} "
            f"(remotion/src/angles.json rigs: {', '.join(known[1:])}; front is the default, not a rule)"
        )
    for name, rule in rules.items():
        lo, hi = (float(v) for v in rule["lengthS"])
        where = f"{_m.MOTION_JSON.name}: framing.angles.rules.{name}"
        # A stretch in another angle is one shot: no longer than any other.
        if rule["kind"] not in ANGLE_KINDS or not 0 < lo <= hi <= float(fc["maxShotS"]):
            raise MotionError(
                f"{where}: kind is one of {', '.join(ANGLE_KINDS)} and lengthS [low, high] > 0, "
                "high at most framing.maxShotS"
            )
        if not rule["shots"] or set(rule["shots"]) - fc["shots"].keys():
            raise MotionError(f"{where}: shots must name at least one of {', '.join(fc['shots'])}")
        if rule["kind"] == "share" and not 0 <= rule["share"][0] <= rule["share"][1] < 0.5:
            raise MotionError(f"{where}: share must be [low, high] under 0.5 (front stays the majority)")
        if rule["kind"] == "beat" and (
            int(rule["maxPerReel"]) < 0
            or rule["pictureS"][0] > rule["pictureS"][1]
            or not isinstance(rule["elsewhere"], bool)
        ):
            raise MotionError(f"{where}: maxPerReel must be >= 0, pictureS [low, high] and elsewhere true or false")
    # The front shot before an angle's cut is at least minShotS (the reel's first one too).
    if min(float(ac["minFrontS"]), float(ac["notBeforeS"])) < float(fc["minShotS"]) or int(ac["attempts"]) < 1:
        raise MotionError(
            f"{_m.MOTION_JSON.name}: framing.angles.minFrontS and notBeforeS must be at least "
            "framing.minShotS and attempts at least 1"
        )
