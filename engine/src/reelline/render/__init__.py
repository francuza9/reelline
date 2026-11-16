"""Rendering: a Renderer turns a script, its timings and the assets into one video per platform.
The host (drawings, mouth shapes, poses, angles) is data: a HostRig folder with a rig.yaml."""

from reelline.render.base import HostRig, Renderer, RenderError, RenderJob, RigError, load_rig

__all__ = ["HostRig", "RenderError", "RenderJob", "Renderer", "RigError", "load_rig"]
