"""fx.json read through the style's overrides and checked; destination and city names."""

from __future__ import annotations

import json
from functools import lru_cache

from reelline.media import style as reel_style
from reelline.media.fx.base import DESTINATIONS_JSON, FX_JSON, GEORGIA_JSON, TEMPLATES, FxError


def config() -> dict:
    """fx.json with the style's `reel` overrides (reelline/media/style.py Style.fx_reel: straight's more cards,
    on paragraphs in a row and before the tag, closer together), checked for what this module reads."""
    return _config(reel_style.current().name)


@lru_cache(maxsize=4)
def _config(style_name: str) -> dict:
    try:
        spec = reel_style.over(
            json.loads(FX_JSON.read_text(encoding="utf-8")), {"reel": reel_style.STYLES[style_name].fx_reel}
        )
        reel, popout = spec["reel"], spec["placements"]["popout"]
        for key in ("holdS", "minHoldS", "gapS", "outroGapS", "captionPadS", "minPerScript", "maxPerReel", "queueLagS"):
            float(reel[key])
        for key in ("consecutive", "beforeTag", "queue"):
            if not isinstance(reel[key], bool):
                raise TypeError(f"reel.{key} must be true or false")
        missing = [t for t in TEMPLATES if t not in spec["templates"]]
        float(popout["leadS"]), float(popout["back"]["durationS"])
        opening = spec["opening"]
        if not isinstance(opening["fromFrame0"], bool):
            raise TypeError("opening.fromFrame0 must be true or false")
        float(opening["minHoldS"])
        for key in ("fromScale", "durationS", "damping"):
            float(opening["settle"][key])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise FxError(f"Can't read {FX_JSON}: {exc}") from exc
    if missing:
        raise FxError(f"{FX_JSON.name}: no templates entry for {', '.join(missing)}")
    if reel["placement"] != "popout":
        raise FxError(f"{FX_JSON.name}: reel.placement must be popout (the one chosen), got {reel['placement']!r}")
    if not 1 <= reel["minPerScript"] <= reel["maxPerReel"]:
        raise FxError(f"{FX_JSON.name}: reel.minPerScript must be in 1..maxPerReel")
    return spec


config.cache_clear = _config.cache_clear  # type: ignore[attr-defined]


@lru_cache(maxsize=1)
def countries() -> dict[str, str]:
    """Every name abroad takes (destinations.json places and aliases) -> its place's name."""
    try:
        spec = json.loads(DESTINATIONS_JSON.read_text(encoding="utf-8"))
        names = {name: name for name in spec["places"]}
        names.update({alias: target for alias, target in spec.get("aliases", {}).items() if target in spec["places"]})
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise FxError(f"Can't read {DESTINATIONS_JSON}: {exc}") from exc
    return names


@lru_cache(maxsize=1)
def cities() -> frozenset[str]:
    """The cities location's map has a pin position for (georgia.json)."""
    return frozenset(json.loads(GEORGIA_JSON.read_text(encoding="utf-8"))["cities"])
