"""Background rotation, the reel's mood and the backgrounds report."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from reelline.config import conf
from reelline.media import style as reel_style
from reelline.media.backgrounds import (
    BackgroundError,
    all_backgrounds,
    enabled_backgrounds,
    next_background,
    orphan_sidecars,
    sidecar_overrides,
    validate,
)
from reelline.pipeline.produce.base import ProduceError


def last_background(conn: sqlite3.Connection) -> str | None:
    """The background of the latest reel that rendered (whatever happened to it since:
    approved, superseded, ...); None before the first."""
    row = conn.execute(
        "SELECT background FROM reels WHERE status != 'render_failed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["background"] if row else None


def rotation_pick(conn: sqlite3.Connection) -> str:
    """The rotation's next background."""
    try:
        return next_background([b.name for b in enabled_backgrounds()], last_background(conn))
    except BackgroundError as exc:
        raise ProduceError(str(exc)) from None


@dataclass(frozen=True)
class BackgroundChoice:
    name: str
    source: str  # "rotation" | "manual" | "kept" (a regeneration keeps its reel's background)


def choose_background(conn: sqlite3.Connection, override: str | None) -> BackgroundChoice:
    """--background (any existing background, disabled ones too) or the rotation's next."""
    if override is None:
        return BackgroundChoice(rotation_pick(conn), "rotation")
    names = [b.name for b in all_backgrounds()]
    if override not in names:
        raise ProduceError(f"No background {override!r} in assets/backgrounds/ (have: {', '.join(names)})")
    return BackgroundChoice(override, "manual")


def choose_mood(conn: sqlite3.Connection, pick: sqlite3.Row, script: sqlite3.Row) -> str:
    """The reel's rest-mouth mood. One default for now: the style's (reelline/media/style.py: straight's
    happy, never smug), else steps.produce.default_mood; the hook for a per-reel choice (from the
    script's @style lines, the topic, ...)."""
    return reel_style.current().mood or conf().steps.produce.default_mood


def backgrounds_report(conn: sqlite3.Connection) -> list[str]:
    """Enabled and disabled backgrounds (with sidecars and problems), and the rotation."""
    lines = []
    everything = all_backgrounds()
    for title, group in (
        ("Enabled (rotation order)", [b for b in everything if b.enabled]),
        ("Disabled", [b for b in everything if not b.enabled]),
    ):
        lines.append(f"{title}:" if group else f"{title}: none")
        for bg in group:
            notes = []
            if bg.sidecar:
                try:
                    over = sidecar_overrides(bg)
                    notes.append(f"sidecar {bg.sidecar.name}: {_flat(over) or 'empty'}")
                except BackgroundError as exc:
                    notes.append(f"INVALID sidecar: {exc}")
            try:
                validate(bg)
            except BackgroundError as exc:
                notes.append(f"INVALID: {exc}")
            lines.append(f"  {bg.name}" + (f"  ({'; '.join(notes)})" if notes else ""))
    for orphan in orphan_sidecars():
        lines.append(f"Orphan sidecar (no PNG): {orphan.name}")
    last = last_background(conn)
    lines.append(f"Last used: {last or 'none yet'}")
    try:
        lines.append(f"Next: {rotation_pick(conn)}")
    except ProduceError as exc:
        lines.append(f"Next: none ({exc})")
    return lines


def _flat(over: dict, prefix: str = "") -> str:
    parts = []
    for key, value in over.items():
        path = f"{prefix}.{key}" if prefix else key
        parts.append(_flat(value, path) if isinstance(value, dict) else f"{path}={value}")
    return ", ".join(p for p in parts if p)
