"""How an image sits on the wall TV's face.

Photo mode ("cover"): crop to the face's aspect ratio around the subject bbox
(center crop without one), never below the size that fills the face at the largest
Ken Burns zoom, never narrower than FRAMING["min_crop_frac"] of the largest crop.
The focal point is the subject's center inside the crop; Remotion zooms toward it.

Logo/graphic mode ("contain"): no crop; the image sits on a flat panel whose color
depends on the logo's mean luminance over its opaque pixels.

Crop and focal point are normalized: crop x/y/w/h as fractions of the image's
width/height, focal x/y as fractions of the crop.
"""

from __future__ import annotations

from PIL import Image, ImageStat

from reelline.media.images.config import CANDIDATES, FRAMING, Screen


def resolution_problem(width: int, height: int, screen: Screen) -> str | None:
    """Why a width x height image is too small for photo mode, or None if it's fine:
    the largest crop at the face's aspect must fill the face at the largest zoom."""
    crop_w = min(width, height * screen.aspect)
    need_w, need_h = screen.min_crop
    if crop_w + 1e-6 < need_w:
        return (
            f"too small: {width}x{height}, its {screen.aspect:.2f}:1 crop is "
            f"{crop_w:.0f}x{crop_w / screen.aspect:.0f}, needs {need_w:.0f}x{need_h:.0f}"
        )
    return None


def aspect_problem(width: int, height: int) -> str | None:
    """Why a photo is too tall for the screen (width / height below
    CANDIDATES["min_aspect"]), or None. Callers exempt SVGs and known logos."""
    if width < CANDIDATES["min_aspect"] * height:
        return f"too tall: {width}x{height} ({width / height:.2f}:1, needs {CANDIDATES['min_aspect']:g}:1 or wider)"
    return None


def _valid_bbox(bbox: dict | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    x = min(max(float(bbox["x"]), 0.0), 1.0)
    y = min(max(float(bbox["y"]), 0.0), 1.0)
    w = min(max(float(bbox["w"]), 0.0), 1.0 - x)
    h = min(max(float(bbox["h"]), 0.0), 1.0 - y)
    return (x, y, w, h) if w > 0 and h > 0 else None


def cover_crop(width: int, height: int, bbox: dict | None, screen: Screen) -> tuple[dict, dict]:
    """(crop, focal) for photo mode; bbox is the vision's normalized subject_bbox."""
    aspect = screen.aspect
    max_w = min(width, height * aspect)
    min_w = min(max_w, max(screen.min_crop[0], FRAMING["min_crop_frac"] * max_w))
    box = _valid_bbox(bbox)
    if box is None:
        cw, cx, cy = max_w, width / 2, height / 2
    else:
        bx, by, bw, bh = box[0] * width, box[1] * height, box[2] * width, box[3] * height
        pad = FRAMING["subject_pad"]
        need_w = max(bw * (1 + 2 * pad), bh * (1 + 2 * pad) * aspect)
        cw = min(max(need_w, min_w), max_w)
        cx, cy = bx + bw / 2, by + bh / 2
    ch = cw / aspect
    x = min(max(cx - cw / 2, 0.0), width - cw)
    y = min(max(cy - ch / 2, 0.0), height - ch)
    crop = {"x": x / width, "y": y / height, "w": cw / width, "h": ch / height}
    if box is None:
        focal = {"x": 0.5, "y": 0.5}
    else:
        focal = {
            "x": min(max((cx - x) / cw, 0.0), 1.0),
            "y": min(max((cy - y) / ch, 0.0), 1.0),
        }
    return {k: round(v, 5) for k, v in crop.items()}, {k: round(v, 5) for k, v in focal.items()}


def mean_luminance(image: Image.Image) -> float:
    """Mean luma (0..1) over the opaque pixels; ValueError if none are opaque."""
    rgba = image.convert("RGBA")
    mask = rgba.getchannel("A").point(lambda a: 255 if a >= FRAMING["opaque_alpha"] else 0)
    if mask.getbbox() is None:
        raise ValueError("image has no opaque pixels")
    return ImageStat.Stat(rgba.convert("L"), mask).mean[0] / 255


def panel_color(luminance: float) -> str:
    """Dark logo -> light panel, light logo -> dark panel."""
    return FRAMING["panel_light"] if luminance < FRAMING["luminance_threshold"] else FRAMING["panel_dark"]
