"""The camera's other angles (channel docs/fx.md "Motion"): The host in 3/4 view in the room's 3/4 art,
and from behind facing the TV. remotion/src/angles.json is the art (rigs: placement, mouth,
eyes, stalks; backgrounds: each room's 3/4 background and the TV's quad on it), read here and
by remotion/src/angles.ts; motion.json framing.angles is when (reelline/media/motion.py plan_angles()).

"front" is the scene as it always was (assets/anchors.json, remotion/src/screen.json); every
other angle is a rig in angles.json. A rig with scene "angled" needs the reel's background to
have art for it (available()); one with scene "front" plays in the reel's own background.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.paths import graphics_file
from reelline.render.registry import get_rig

ANGLES_JSON = graphics_file(conf().channel.graphics_dir, "angles.json")
ASSETS = conf().channel.dir / "assets"
FRONT = "front"
SCENES = ("front", "angled")
FRAME = (1080, 1920)


class AngleError(ReellineError, ValueError):
    """angles.json can't be read, doesn't hold what this module reads, or names a missing file."""


def _quad(value, where: str) -> list[tuple[float, float]]:
    pts = [(float(x), float(y)) for x, y in value]
    if len(pts) != 4:
        raise AngleError(f"{ANGLES_JSON.name}: {where} needs 4 corners, got {len(pts)}")
    return pts


@lru_cache(maxsize=1)
def config() -> dict:
    try:
        spec = json.loads(ANGLES_JSON.read_text(encoding="utf-8"))
        rigs = spec["rigs"]
        for name, rig in rigs.items():
            where = f"rigs.{name}"
            if name == FRONT or rig["scene"] not in SCENES:
                raise AngleError(
                    f"{ANGLES_JSON.name}: {where}: front isn't a rig, and scene is one of {', '.join(SCENES)}"
                )
            w, h = (int(v) for v in rig["size"])
            for key in ("left", "top", "scale"):
                float(rig["placement"][key])
            float(rig["seatY"]), float(rig["stalks"]["cutY"]), float(rig["stalks"]["splitX"])
            if rig["mouth"] is not None:
                m = rig["mouth"]
                str(m["dir"]), float(m["scale"]), float(m["center"][0]), float(m["center"][1])
                float(m["pngSize"][0]), float(m["pngSize"][1])
                for y, x in m["clipX"]:
                    float(y), float(x)
            if rig["eyes"] is not None:
                for side in ("left", "right"):
                    eye = rig["eyes"][side]
                    for part, keys in (
                        ("white", ("cx", "cy", "rx", "ry", "rotate")),
                        ("pupil", ("cx", "cy", "rx", "ry", "rotate")),
                        ("highlight", ("cx", "cy", "r")),
                    ):
                        for key in keys:
                            float(eye[part][key])
                    if len(eye["box"]) != 4:
                        raise AngleError(f"{ANGLES_JSON.name}: {where}.eyes.{side}.box needs 4 numbers")
                float(rig["coverPx"]), float(rig["pupilTravel"]), float(rig["lidStrokePx"])
                float(rig["tvPupil"][0]), float(rig["tvPupil"][1])
                for key in ("yellow", "outline", "white"):
                    str(rig["colors"][key])
            if (
                not float(rig["placement"]["scale"]) > 0
                or not 0 < rig["seatY"] <= h
                or not 0 < rig["stalks"]["cutY"] < rig["seatY"]
                or not 0 < rig["stalks"]["splitX"] < w
            ):
                raise AngleError(
                    f"{ANGLES_JSON.name}: {where}: needs 0 < stalks.cutY < seatY <= its height and "
                    "stalks.splitX inside its width"
                )
        for bg, arts in spec["backgrounds"].items():
            if bg == "note":
                continue
            for angle, art in arts.items():
                where = f"backgrounds.{bg}.{angle}"
                if angle not in rigs or rigs[angle]["scene"] != "angled":
                    raise AngleError(
                        f"{ANGLES_JSON.name}: {where}: {angle!r} isn't a rig with scene angled "
                        f"(have: {', '.join(rigs)})"
                    )
                str(art["file"])
                _quad(art["frame"], f"{where}.frame"), _quad(art["face"], f"{where}.face")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, AngleError):
            raise
        raise AngleError(f"Can't read {ANGLES_JSON}: {exc}") from exc
    return spec


def names() -> list[str]:
    """Every angle: front and the rigs."""
    return [FRONT, *config()["rigs"]]


def art_for(background: str) -> dict[str, dict]:
    """The background's (its name, e.g. "bedroom_night") art per angled rig: {angle: {file, frame, face}}."""
    return {k: v for k, v in config()["backgrounds"].get(background, {}).items()}


def available(background: str | None) -> list[str]:
    """The angles a reel in `background` can cut to: front, every rig with scene front, and each
    angled one the background has art for (None: every angle)."""
    rigs = config()["rigs"]
    if background is None:
        return names()
    art = art_for(background)
    return [FRONT, *(a for a, r in rigs.items() if r["scene"] == "front" or a in art)]


def files(background: str | None) -> list[Path]:
    """The angles' files under assets/ a render stages (rigs and mouths, and `background`'s angled
    art), each staged into the Remotion public dir at its path under assets/."""
    out: list[Path] = []
    for rig in config()["rigs"].values():
        out.append(ASSETS / rig["image"])
        if rig["mouth"] is not None:
            out += sorted((ASSETS / rig["mouth"]["dir"]).glob("*.png"))
    if background is not None:
        out += [ASSETS / art["file"] for art in art_for(background).values()]
    return out


def check_files(background: str | None = None) -> None:
    """Raises AngleError naming a missing file: a rig's art, a mouth for every assets/mouths code,
    an angled background that isn't 1080x1920 (every background's when `background` is None)."""
    from PIL import Image

    cfg = config()
    codes = [p.name for p in get_rig().mouth_files()]
    for name, rig in cfg["rigs"].items():
        if not (ASSETS / rig["image"]).is_file():
            raise AngleError(f"{ANGLES_JSON.name} rigs.{name}: {rig['image']} isn't in assets/ (tools/angles/build.py)")
        if rig["mouth"] is not None:
            missing = [c for c in codes if not (ASSETS / rig["mouth"]["dir"] / c).is_file()]
            if missing:
                raise AngleError(
                    f"{ANGLES_JSON.name} rigs.{name}: no {', '.join(missing)} in assets/{rig['mouth']['dir']}"
                )
    rooms = [b for b in cfg["backgrounds"] if b != "note"] if background is None else [background]
    for room in rooms:
        for angle, art in art_for(room).items():
            path = ASSETS / art["file"]
            try:
                with Image.open(path) as im:
                    size = im.size
            except OSError as exc:
                raise AngleError(f"{ANGLES_JSON.name} backgrounds.{room}.{angle}: {art['file']}: {exc}") from None
            if size != FRAME:
                raise AngleError(
                    f"{ANGLES_JSON.name} backgrounds.{room}.{angle}: {art['file']} is {size[0]}x{size[1]}; "
                    f"it must be {FRAME[0]}x{FRAME[1]}"
                )


def face_boxes(angle: str) -> dict[str, dict[str, float]]:
    """A rig's face at rest in output pixels (remotion/src/angles.ts angleFaceBoxes): each eye's
    outer box ("left eye", "right eye") and the mouth image's box ("mouth"); none for a rig
    without them (from behind)."""
    rig = config()["rigs"][angle]
    p = rig["placement"]
    left, top, s = float(p["left"]), float(p["top"]), float(p["scale"])

    def box(x0: float, y0: float, x1: float, y1: float) -> dict[str, float]:
        return {"left": left + x0 * s, "top": top + y0 * s, "right": left + x1 * s, "bottom": top + y1 * s}

    out = {}
    if rig["eyes"] is not None:
        for side in ("left", "right"):
            out[f"{side} eye"] = box(*rig["eyes"][side]["box"])
    if rig["mouth"] is not None:
        m = rig["mouth"]
        (cx, cy), (w, h), k = m["center"], m["pngSize"], float(m["scale"])
        out["mouth"] = box(cx - w * k / 2, cy - h * k / 2, cx + w * k / 2, cy + h * k / 2)
    return out


def tv_boxes(angle: str) -> list[dict[str, float]]:
    """An angled rig's TV in every room it has art for: the frame quad's bounding box (output
    pixels), for the framing check (a shot that isn't close keeps the whole TV)."""
    out = []
    for room, arts in config()["backgrounds"].items():
        if room == "note" or angle not in arts:
            continue
        xs, ys = zip(*_quad(arts[angle]["frame"], f"backgrounds.{room}.{angle}.frame"), strict=False)
        out.append({"left": min(xs), "top": min(ys), "right": max(xs), "bottom": max(ys), "room": room})
    return out
