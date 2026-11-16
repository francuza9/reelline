"""render.renderer -> the Renderer: get_renderer(). The adapter is imported when asked for."""

from __future__ import annotations

from typing import Any

from reelline.config import conf
from reelline.render.base import HostRig, Renderer, load_rig


def get_renderer(kind: str | None = None, **kw: Any) -> Renderer:
    kind = kind or conf().render.renderer
    if kind == "remotion":
        from reelline.render.remotion import RemotionRenderer

        return RemotionRenderer(**kw)
    raise ValueError(f"no renderer {kind!r} (render.renderer: remotion)")


def get_rig() -> HostRig:
    """The channel's host: render.rig_dir (channel-relative) with its rig.yaml."""
    return load_rig(conf().render.rig_dir)
