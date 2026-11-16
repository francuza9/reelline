"""The settled card's box and the captions and eyes it must clear (QA)."""

from __future__ import annotations

import math
import re
from pathlib import Path

from reelline.config import conf
from reelline.media.fx.base import FRAME_H, FRAME_W, REMOTION_SRC, FxError, config
from reelline.paths import mascot_file

# ---- Geometry (QA) ----------------------------------------------------------------------


def card_bounds(frame_w: int = FRAME_W) -> dict[str, float]:
    """The settled card's bounding box in output pixels, its tilt included (Fx.tsx popoutBounds)."""
    po = config()["placements"]["popout"]
    w, h = frame_w * po["widthRatio"], po["height"]
    a = math.radians(abs(po["tiltDeg"]))
    hw = w / 2 * math.cos(a) + h / 2 * math.sin(a)
    hh = h / 2 * math.cos(a) + w / 2 * math.sin(a)
    cx, cy = frame_w / 2, po["centerY"]
    return {"left": cx - hw, "top": cy - hh, "right": cx + hw, "bottom": cy + hh}


def _ts_number(path: Path, name: str) -> float:
    m = re.search(rf"\b{name}\s*[:=]\s*([\d.]+)", path.read_text(encoding="utf-8"))
    if m is None:
        raise FxError(f"{name} not found in {path}")
    return float(m.group(1))


def caption_top(lines: int) -> float:
    """The top of a caption block of `lines` lines in output pixels (Captions.tsx CAPTIONS)."""
    path = REMOTION_SRC / "Captions.tsx"
    return _ts_number(path, "bottomY") - lines * _ts_number(path, "fontSizePx") * _ts_number(path, "lineHeight")


def eye_tops(anchors: dict, frame_w: int = FRAME_W, frame_h: int = FRAME_H) -> float:
    """The top of the mascot's eyes (their outer circles) at rest, in output pixels (the mascot component's faceBoxes)."""
    path = mascot_file(conf().channel.dir, conf().channel.mascot_component)
    body_w, wheels = _ts_number(path, "BODY_WIDTH"), _ts_number(path, "WHEELS_ABOVE_BOTTOM")
    scale = body_w / anchors["canvas"]["width"]
    top = frame_h - wheels - anchors["chair"]["wheel_bottom_y"] * scale
    return min(top + (e["center"][1] - e["radius_outer"]) * scale for e in anchors["eyes"].values())


def safe_problems(frame_w: int = FRAME_W) -> list[str]:
    """Where the settled card is outside placements.popout.safe: the top bar, the side margins,
    a one-line caption's top (minus captionClearancePx)."""
    po = config()["placements"]["popout"]
    s, b = po["safe"], card_bounds(frame_w)
    limit = caption_top(int(s["captionMaxLines"])) - s["captionClearancePx"]
    checks = [
        (b["top"] < s["topPx"], f"top {b['top']:.0f} above safe.topPx {s['topPx']}"),
        (b["left"] < s["sideMarginPx"], f"left {b['left']:.0f} inside the {s['sideMarginPx']} px margin"),
        (b["right"] > frame_w - s["sideMarginPx"], f"right {b['right']:.0f} inside the {s['sideMarginPx']} px margin"),
        (
            b["bottom"] > limit,
            f"bottom {b['bottom']:.0f} below a one-line caption's top minus the clearance ({limit:.0f})",
        ),
    ]
    return [what for bad, what in checks if bad]
