"""The render: its result record and the real renderer."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from reelline.paths import SCRIPTS_DIR
from reelline.pipeline.produce.base import RenderFailed


@dataclass
class Rendered:
    """What produce needs from a render (scripts/render_reel.py RenderResult)."""

    name: str
    mp4: Path
    cover: Path | None
    duration: float
    images_json: Path | None
    attributions: Path | None
    check_these: list[dict] = field(default_factory=list)
    tts_cost_usd: float | None = None  # the reel's TTS (cached takes at their cost)
    tts_new_cost_usd: float | None = None  # this render's TTS calls
    image_calls: list[dict] = field(default_factory=list)  # this render's paid image-model calls
    tts_calls: list[dict] = field(default_factory=list)  # this render's fresh TTS clips
    # The cold open (reelline/media/gags.py): the gag (None: none), its length, the mode it was decided under.
    gag: str | None = None
    gag_s: float | None = None
    gag_mode: str | None = None

    @property
    def paid_calls(self) -> list[dict]:
        return self.image_calls + self.tts_calls

    def gag_fields(self) -> dict:
        """The reels row's cold open columns (migration 28)."""
        return {"gag": self.gag, "gag_s": self.gag_s, "gag_mode": self.gag_mode}


# (script file, background, mood, paragraphs whose TTS to redo, **RENDER_OPTIONS)
Renderer = Callable[..., Rendered]
# Options a regeneration passes to the first render only (QA's re-renders reuse what it made).
RENDER_OPTIONS = ("fresh_tts", "refresh_images")


def render_reel(
    script_path: Path,
    background: str,
    mood: str,
    regen: list[int] = (),
    *,
    fresh_tts: bool = False,
    refresh_images: bool = False,
) -> Rendered:
    """The real renderer: scripts/render_reel.py render(); its RenderError -> RenderFailed.
    fresh_tts: every paragraph's TTS again (the pinned tag stays); refresh_images: every
    paragraph's image picked again."""
    scripts_dir = str(SCRIPTS_DIR)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import render_reel as rr

    try:
        r = rr.render(
            script_path,
            rr.RenderOptions(
                background=background, mood=mood, regen=list(regen), fresh_tts=fresh_tts, refresh_images=refresh_images
            ),
        )
    except rr.RenderError as exc:
        raise RenderFailed(str(exc), getattr(exc, "paid_calls", [])) from None
    return Rendered(
        r.name,
        r.mp4,
        r.cover,
        r.duration,
        r.images_json,
        r.attributions,
        r.check_these,
        r.tts_cost_usd,
        r.tts_new_cost_usd,
        r.image_calls,
        r.tts_calls,
        r.gag,
        r.gag_s,
        r.gag_mode,
    )


@dataclass
class RenderJob:
    """A rendered reel and what made it (QA may re-roll and render it again)."""

    reel_id: int
    pick: sqlite3.Row
    script: sqlite3.Row
    script_path: Path
    background: str
    mood: str
    rendered: Rendered
    renderer: Renderer
