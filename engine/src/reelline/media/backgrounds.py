"""Scene backgrounds: the drop-in folder assets/backgrounds/ is the only registry.

Every *.png there is a background named by its file stem; a stem starting with
BACKGROUNDS["disabled_prefix"] ("_") is disabled: kept and still checkable
(render_reel.py --background-check), but never picked by the rotation. There is no
other list: add a PNG to add a background, rename it to disable it.

validate() fails loudly, naming the file, unless the PNG is readable, exactly
BACKGROUNDS["size"] and in one of BACKGROUNDS["modes"].

A sidecar <name>.json next to the PNG overrides the wall TV for that background
only: it is deep-merged over remotion/src/screen.json (screen_spec()). Only keys
that exist in screen.json are allowed, objects where screen.json has objects and
values where it has values; anything else fails. Chair and the host positions are
global (assets/anchors.json) and can't be overridden here.

The rotation (next_background()): the enabled backgrounds sorted by name; the next
one follows the last one used, wrapping around. A last one that's gone or disabled
continues from where its name would sort. The history lives in the reels table
(reelline/pipeline/produce.py).
"""

from __future__ import annotations

import bisect
import copy
import json
from dataclasses import dataclass
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.paths import REMOTION_DIR

BACKGROUNDS_DIR = conf().channel.dir / "assets" / "backgrounds"
SCREEN_JSON = REMOTION_DIR / "src" / "screen.json"

BACKGROUNDS = {
    # The reel's frame; a background must be exactly this size.
    "size": (1080, 1920),
    "modes": ("RGB", "RGBA"),
    "disabled_prefix": "_",
    # render_reel.py --background-check: a still with no TTS. A silent track this long,
    # the still at still_s; the sample caption (two lines) and the cover headline.
    "check": {
        "audio_s": 2.0,
        "still_s": 1.0,
        "caption_lines": [list(line) for line in conf().channel.background_check_lines],
        "headline": conf().channel.background_check_headline,
    },
}


class BackgroundError(ReellineError, RuntimeError):
    """A missing, invalid or misconfigured background (the message names the file)."""


@dataclass(frozen=True)
class Background:
    name: str
    path: Path

    @property
    def enabled(self) -> bool:
        return not self.name.startswith(BACKGROUNDS["disabled_prefix"])

    @property
    def file(self) -> str:
        """The file name Remotion gets (under backgrounds/ in its public dir)."""
        return self.path.name

    @property
    def sidecar(self) -> Path | None:
        path = self.path.with_suffix(".json")
        return path if path.is_file() else None


def all_backgrounds(folder: Path | None = None) -> list[Background]:
    """Every background, enabled or not, sorted by name."""
    folder = folder or BACKGROUNDS_DIR
    if not folder.is_dir():
        raise BackgroundError(f"Backgrounds folder not found: {folder}")
    return sorted((Background(p.stem, p) for p in folder.glob("*.png")), key=lambda b: b.name)


def enabled_backgrounds(folder: Path | None = None) -> list[Background]:
    return [b for b in all_backgrounds(folder) if b.enabled]


def orphan_sidecars(folder: Path | None = None) -> list[Path]:
    """Sidecar JSON files with no PNG of the same name (likely a typo)."""
    folder = folder or BACKGROUNDS_DIR
    return sorted(p for p in folder.glob("*.json") if not p.with_suffix(".png").is_file())


def get_background(name: str, folder: Path | None = None) -> Background:
    """The background called `name` (enabled or not)."""
    for bg in all_backgrounds(folder):
        if bg.name == name:
            return bg
    names = ", ".join(b.name for b in all_backgrounds(folder)) or "none"
    raise BackgroundError(f"No background {name!r} in {folder or BACKGROUNDS_DIR} (have: {names})")


def validate(bg: Background) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(bg.path) as im:
            size, mode = im.size, im.mode
            im.load()
    except (OSError, UnidentifiedImageError) as exc:
        raise BackgroundError(f"Background {bg.path.name} isn't a readable image: {exc}") from None
    want = tuple(BACKGROUNDS["size"])
    if size != want:
        raise BackgroundError(f"Background {bg.path.name} is {size[0]}x{size[1]}; it must be {want[0]}x{want[1]}")
    if mode not in BACKGROUNDS["modes"]:
        raise BackgroundError(
            f"Background {bg.path.name} is mode {mode}; it must be one of {', '.join(BACKGROUNDS['modes'])}"
        )


def base_screen() -> dict:
    return json.loads(SCREEN_JSON.read_text(encoding="utf-8"))


def _check_override(over: dict, base: dict, where: str, file: str) -> None:
    for key, value in over.items():
        path = f"{where}.{key}" if where else key
        if key not in base:
            raise BackgroundError(f"Sidecar {file}: unknown key {path!r} (not in {SCREEN_JSON.name})")
        if isinstance(base[key], dict) != isinstance(value, dict):
            want = "an object" if isinstance(base[key], dict) else "a value, not an object"
            raise BackgroundError(f"Sidecar {file}: {path!r} must be {want}, as in {SCREEN_JSON.name}")
        if isinstance(value, dict):
            _check_override(value, base[key], path, file)


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        out[key] = deep_merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def sidecar_overrides(bg: Background, base: dict | None = None) -> dict:
    """The sidecar's overrides, checked against screen.json ({} without a sidecar)."""
    if bg.sidecar is None:
        return {}
    try:
        over = json.loads(bg.sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackgroundError(f"Sidecar {bg.sidecar.name} isn't valid JSON: {exc}") from None
    if not isinstance(over, dict):
        raise BackgroundError(f"Sidecar {bg.sidecar.name} must be a JSON object")
    _check_override(over, base if base is not None else base_screen(), "", bg.sidecar.name)
    return over


def screen_spec(bg: Background) -> dict:
    """screen.json with the background's sidecar deep-merged over it."""
    base = base_screen()
    return deep_merge(base, sidecar_overrides(bg, base))


def next_background(enabled: list[str], last: str | None) -> str:
    """The rotation: the name after `last` in sorted `enabled`, wrapping around; a
    `last` that's gone or disabled continues from where its name would sort."""
    names = sorted(enabled)
    if not names:
        raise BackgroundError(
            f"No enabled backgrounds in {BACKGROUNDS_DIR} (every *.png starts with "
            f"{BACKGROUNDS['disabled_prefix']!r}, or there are none)"
        )
    if last is None:
        return names[0]
    i = bisect.bisect_right(names, last)
    return names[i % len(names)]
