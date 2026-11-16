"""Where things live in the repo checkout (src layout, installed with `pip install -e engine`).

    <REPO_ROOT>/
      engine/               ENGINE_DIR: this package (src/reelline), remotion/, scripts/, tools/, tests/
      channels/<name>/      the channel: config.yaml, prompts, assets, voice, sources.yaml, data, content
      ops/                  deploy/, Makefile
      .env, out/, logs/, creds/, .wrangler/   runtime state, at the repo root

The channel's directory is not a constant: it comes from the config (reelline.config conf().channel.dir),
and every path to a prompt, a source registry, the database or a script file from its settings.
"""

from __future__ import annotations

from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_DIR.parent
REMOTION_DIR = ENGINE_DIR / "remotion"
SCRIPTS_DIR = ENGINE_DIR / "scripts"
TOOLS_DIR = ENGINE_DIR / "tools"
# What a channel without its own graphics data or mascot gets (remotion.config.ts resolves @channel the same way).
DEFAULT_CHANNEL_DIR = REMOTION_DIR / "src" / "channel_default"

# The top directories of a pre-split stored path that are the channel's now (content/reels/auto/...,
# data/...: they lived at the repo root before channels/<name>/ existed).
LEGACY_CHANNEL_DIRS = ("content", "data")


def stored_path(value: str | Path, channel_dir: Path | None = None) -> Path:
    """A path as the database stores it -> the file. Rows hold repo-root-relative paths (out/reels/...,
    channels/the channel's content/...); a pre-split row's content/... or data/... is the channel's
    (channel_dir, default the config's), unless only the old place has the file (data not moved yet).
    Absolute paths stay as they are."""
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] in LEGACY_CHANNEL_DIRS:
        if channel_dir is None:
            from reelline.config import conf

            channel_dir = conf().channel.dir
        moved = channel_dir / path
        return REPO_ROOT / path if not moved.exists() and (REPO_ROOT / path).exists() else moved
    return REPO_ROOT / path


def graphics_file(graphics_dir: Path, name: str) -> Path:
    """The channel's graphics data file `name` (outro.json, fx.json, ...), or the engine's default when the
    channel has none."""
    own = Path(graphics_dir) / name
    return own if own.is_file() else DEFAULT_CHANNEL_DIR / "graphics" / name


def mascot_file(channel_dir: Path, component: str) -> Path:
    """The Remotion component that draws the host: the channel's remotion/<component>, else the engine's
    generic host."""
    own = Path(channel_dir) / "remotion" / component
    return own if own.is_file() else DEFAULT_CHANNEL_DIR / "remotion" / "Mascot.tsx"
