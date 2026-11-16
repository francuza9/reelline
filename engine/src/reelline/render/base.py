"""The Renderer protocol and the HostRig (the host as data).

    rig/                      any folder
      rig.yaml                name, anchors, drawings, mouths, poses, angles (paths relative to it)
      <drawings>.png  mouths/<code>.png  poses/<name>.png  angles/...  anchors.json

rig.yaml:
    name: mascot
    kind: images                   # images (default): the drawings below; svg: the renderer draws the host
                                   # itself from anchors.json (its colors, face and mouth placement), so
                                   # drawings and mouths may be left out (the generic placeholder host)
    anchors: anchors.json          # placement of the face parts, mouth by mood (a JSON file)
    drawings: [mascot.png, chair.png]   # staged at the public dir's root
    mouths: mouths                 # dir of <code>.png, one per mouth shape (A-H, X, X_<mood>)
    poses: poses                   # dir of <name>.png (optional; may be absent)
    angles: angles                 # dir of the other camera angles' art (optional)
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from reelline.core.errors import ReellineError


class RenderError(ReellineError, RuntimeError):
    """A render that can't start or failed (the CLI prints it as FAIL: <message>)."""


class RigError(ReellineError, ValueError):
    """A rig folder or its rig.yaml that is missing something."""


@dataclass(frozen=True)
class HostRig:
    """`kind` "images": drawings and mouth shapes the renderer stages; "svg": drawn by the renderer from
    the anchors alone (nothing to stage)."""

    dir: Path
    name: str
    anchors: Path
    drawings: tuple[Path, ...]
    mouths: Path | None
    poses: Path | None = None
    angles: Path | None = None
    kind: str = "images"

    def mouth_files(self) -> list[Path]:
        return sorted(self.mouths.glob("*.png")) if self.mouths else []

    def pose_files(self) -> list[Path]:
        return sorted(self.poses.glob("*.png")) if self.poses else []

    def stage(self, public_dir: Path) -> None:
        """The host's drawings at the root of the renderer's public dir and its mouths under mouths/."""
        (public_dir / "mouths").mkdir(parents=True, exist_ok=True)
        # An svg host has nothing to copy.
        for path in self.drawings:
            shutil.copy2(path, public_dir / path.name)
        for png in self.mouth_files():
            shutil.copy2(png, public_dir / "mouths" / png.name)


def load_rig(directory: Path) -> HostRig:
    """The rig in `directory` (its rig.yaml); RigError naming what is missing."""
    directory = Path(directory)
    path = directory / "rig.yaml"
    if not path.is_file():
        raise RigError(f"{directory}: no rig.yaml")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RigError(f"{path}: {exc}") from None
    if not isinstance(raw, dict):
        raise RigError(f"{path}: expected a mapping")
    kind = raw.get("kind", "images")
    if kind not in ("images", "svg"):
        raise RigError(f"{path}: kind must be images or svg, not {kind!r}")
    for key in ("name", "anchors") + (("drawings", "mouths") if kind == "images" else ()):
        if not raw.get(key):
            raise RigError(f"{path}: {key} is required")
    drawings = raw.get("drawings") or []
    if not isinstance(drawings, list) or not all(isinstance(d, str) for d in drawings):
        raise RigError(f"{path}: drawings must be a list of file names")

    def file(rel: str) -> Path:
        p = directory / rel
        if not p.is_file():
            raise RigError(f"{path}: {rel} isn't a file in {directory}")
        return p

    def folder(rel: str) -> Path:
        p = directory / rel
        if not p.is_dir():
            raise RigError(f"{path}: {rel} isn't a folder in {directory}")
        return p

    mouths = folder(raw["mouths"]) if raw.get("mouths") else None
    if kind == "images" and mouths and not list(mouths.glob("*.png")):
        raise RigError(f"{path}: no mouth shapes (*.png) in {raw['mouths']}")
    return HostRig(
        directory,
        str(raw["name"]),
        file(raw["anchors"]),
        tuple(file(d) for d in drawings),
        mouths,
        folder(raw["poses"]) if raw.get("poses") else None,
        folder(raw["angles"]) if raw.get("angles") else None,
        kind,
    )


@dataclass
class RenderJob:
    """What a Renderer needs: the script's words as the composition's props (captions, cues, motion,
    cards: JSON-able), the audio, the host, the working dir and the platforms to make."""

    props: dict[str, Any]
    audio: Path
    rig: HostRig
    work: Path
    platforms: tuple[str, ...]
    public_dir: Path | None = None
    assets: dict[str, Path] = field(default_factory=dict)  # name -> file the composition shows
    frames: int | None = None


@runtime_checkable
class Renderer(Protocol):
    name: str

    def render(self, job: RenderJob) -> dict[str, Path]:
        """One video per platform: {platform: path}. Raises RenderError."""
        ...

    def still(
        self, composition: str, out: Path, props: dict[str, Any], public_dir: Path, *, frame: int | None = None
    ) -> Path:
        """One frame (or the composition's still) as an image."""
        ...
