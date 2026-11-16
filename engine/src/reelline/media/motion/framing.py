"""The camera's geometry: where each shot puts the mascot's face on screen, and the checks on it."""

from __future__ import annotations

import json

from reelline.config import conf
from reelline.media import angles as angle_art
from reelline.media.motion.base import MOTION_JSON, SCREEN_JSON, MotionError, config
from reelline.render.registry import get_rig

# ---- The camera's geometry -----------------------------------------------------------------------


def _origin(value, frame_w: float, frame_h: float) -> tuple[float, float]:
    """A transform origin in output pixels: "50% 60%" (CSS, of the frame) or [x, y]."""
    if isinstance(value, str):
        x, y = (float(p.rstrip("%")) / 100 for p in value.split())
        return x * frame_w, y * frame_h
    return float(value[0]), float(value[1])


def face_boxes(anchors: dict) -> dict[str, dict[str, float]]:
    """The mascot's face at rest in output pixels (the mascot component's faceBoxes): each eye's outer
    circle ("left eye", "right eye") and the mouth image's box ("mouth")."""
    from reelline.media.fx import FRAME_H, FRAME_W, _ts_number
    from reelline.paths import mascot_file

    path = mascot_file(conf().channel.dir, conf().channel.mascot_component)
    body_w, wheels = _ts_number(path, "BODY_WIDTH"), _ts_number(path, "WHEELS_ABOVE_BOTTOM")
    scale = body_w / anchors["canvas"]["width"]
    left, top = (FRAME_W - body_w) / 2, FRAME_H - wheels - anchors["chair"]["wheel_bottom_y"] * scale

    def box(x0: float, y0: float, x1: float, y1: float) -> dict[str, float]:
        return {
            "left": left + x0 * scale,
            "top": top + y0 * scale,
            "right": left + x1 * scale,
            "bottom": top + y1 * scale,
        }

    out = {}
    for side in ("left", "right"):
        (cx, cy), r = anchors["eyes"][side]["center"], anchors["eyes"][side]["radius_outer"]
        out[f"{side} eye"] = box(cx - r, cy - r, cx + r, cy + r)
    (mw, mh), (mx, my) = anchors["mouth"]["png_size"], anchors["mouth"]["anchor"]
    out["mouth"] = box(mx - mw / 2, my - mh / 2, mx + mw / 2, my + mh / 2)
    return out


def on_screen(box: dict[str, float], shot: dict, push: float = 1.0) -> dict[str, float]:
    """`box` (scene pixels) on screen: the push-in (zoom.origin) inside the shot (scale about origin)."""
    from reelline.media.fx import FRAME_H, FRAME_W

    zx, zy = _origin(config()["zoom"]["origin"], FRAME_W, FRAME_H)
    sx, sy = _origin(shot["origin"], FRAME_W, FRAME_H)
    s = float(shot["scale"])

    def x(v: float) -> float:
        return sx + s * (zx + push * (v - zx) - sx)

    def y(v: float) -> float:
        return sy + s * (zy + push * (v - zy) - sy)

    return {"left": x(box["left"]), "top": y(box["top"]), "right": x(box["right"]), "bottom": y(box["bottom"])}


def max_push(shot: dict) -> float:
    """The largest push-in a shot can take: none in a close shot."""
    cfg = config()
    return 1.0 if shot["close"] else max(float(cfg["zoom"]["scale"][1]), float(cfg["beats"]["zoomScale"][1]))


def framing_problems(name: str, shot: dict, anchors: dict, screen: dict, angle: str = "front") -> list[str]:
    """Where a shot breaks framing.safe (module docstring), with no push-in and with the
    largest it can take: the mascot's eyes and mouth (rigPadPx bigger all round) under the caption
    block's bottom (+ captionClearancePx), below bottomPx or within sideMarginPx of a side; in
    cardShots, under the settled card; a non-close shot cutting off the TV. `angle`: the shot
    in another angle (framing.angles), its rig's face (reelline/media/angles.py face_boxes(); none from
    behind) and, for an angled one, the TV in every room's 3/4 art; the card's rule only when
    a card may be out in it (cardAngles)."""
    from reelline.media.fx import FRAME_H, FRAME_W, caption_top, card_bounds

    fc = config()["framing"]
    safe = fc["safe"]
    pad, captions = float(safe["rigPadPx"]), caption_top(0) + float(safe["captionClearancePx"])
    front = angle == angle_art.FRONT
    faces = face_boxes(anchors) if front else angle_art.face_boxes(angle)
    label = name if front else f"{name} ({angle})"
    cards_out = angle in fc["angles"]["cardAngles"]
    problems = []
    for push in sorted({1.0, max_push(shot)}):
        at = f" at a {push:g}x push-in" if push != 1 else ""
        for what, box in faces.items():
            b = on_screen(
                {
                    "left": box["left"] - pad,
                    "top": box["top"] - pad,
                    "right": box["right"] + pad,
                    "bottom": box["bottom"] + pad,
                },
                shot,
                push,
            )
            if b["top"] < captions:
                problems.append(
                    f"{label}: the mascot's {what} top {b['top']:.0f} is above the captions' bottom {captions:.0f}{at}"
                )
            if b["bottom"] > float(safe["bottomPx"]):
                problems.append(
                    f"{label}: the mascot's {what} bottom {b['bottom']:.0f} is below bottomPx {safe['bottomPx']}{at}"
                )
            if b["left"] < float(safe["sideMarginPx"]) or b["right"] > FRAME_W - float(safe["sideMarginPx"]):
                problems.append(
                    f"{label}: the mascot's {what} ({b['left']:.0f}-{b['right']:.0f}) is within "
                    f"sideMarginPx {safe['sideMarginPx']} of a side{at}"
                )
            if name in fc["cardShots"] and cards_out and push == 1 and b["top"] < card_bounds()["bottom"]:
                problems.append(
                    f"{label}: the mascot's {what} top {b['top']:.0f} is under the card (bottom "
                    f"{card_bounds()['bottom']:.0f})"
                )
    if not shot["close"]:
        s = screen["screen"]
        tvs = (
            [{"left": s["x"], "top": s["y"], "right": s["x"] + s["width"], "bottom": s["y"] + s["height"]}]
            if front or angle_art.config()["rigs"][angle]["scene"] == "front"
            else angle_art.tv_boxes(angle)
        )
        for box in tvs:
            tv = on_screen(box, shot)
            if tv["left"] < 0 or tv["top"] < 0 or tv["right"] > FRAME_W or tv["bottom"] > FRAME_H:
                problems.append(
                    f"{label}: the TV ({tv['left']:.0f}, {tv['top']:.0f}, {tv['right']:.0f}, "
                    f"{tv['bottom']:.0f}"
                    + (f"; {box['room']}" if "room" in box else "")
                    + ") is cut off in a shot that isn't close"
                )
    return problems


def check_framing(anchors: dict | None = None, screen: dict | None = None) -> None:
    """Raises MotionError when a framing.shots entry, or a shot framing.angles.rules allows in
    another angle, breaks framing.safe (framing_problems())."""
    anchors = anchors or json.loads(get_rig().anchors.read_text(encoding="utf-8"))
    screen = screen or json.loads(SCREEN_JSON.read_text(encoding="utf-8"))
    fc = config()["framing"]
    problems = [p for name, shot in fc["shots"].items() for p in framing_problems(name, shot, anchors, screen)]
    if fc["angles"]["enabled"]:
        problems += [
            p
            for angle, rule in fc["angles"]["rules"].items()
            for name in rule["shots"]
            for p in framing_problems(name, fc["shots"][name], anchors, screen, angle)
        ]
    if problems:
        raise MotionError(f"{MOTION_JSON.name} framing.shots: " + "; ".join(problems))
