"""Shared constants, the error, and small helpers every motion submodule uses."""

from __future__ import annotations

import random

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.paths import REMOTION_DIR, graphics_file

MOTION_JSON = graphics_file(conf().channel.graphics_dir, "motion.json")
SCREEN_JSON = REMOTION_DIR / "src" / "screen.json"
INF = float("inf")


class MotionError(ReellineError, ValueError):
    """motion.json can't be read or doesn't hold what this module reads."""


def config() -> dict:
    """motion.config(), looked up at call time (tests patch it on the package)."""
    from reelline.media import motion

    return motion.config()


def _seeded(what: str, k: int, lo: float, hi: float) -> float:
    return lo + random.Random(f"{config()['seed']}:{what}:{k}").random() * (hi - lo)


def _overlaps(a: tuple[float, float], spans: list[tuple[float, float]]) -> tuple[float, float] | None:
    return next((s for s in spans if a[0] < s[1] and a[1] > s[0]), None)


def _why(span: tuple[float, float]) -> str:
    return "the end card" if span[1] == INF else f"the fact graphic at {span[0]:.2f}s"
