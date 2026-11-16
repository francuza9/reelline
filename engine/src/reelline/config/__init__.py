"""The one config: load a channel's settings, layered, and hand the same object to every reader.

Layers, last wins:
  1. the schema's defaults (reelline/config/schema.py: generic, no channel's values)
  2. the channel's config.yaml (<channel dir>/config.yaml; paths in it relative to the channel dir)
  3. env vars REELLINE__SECTION__KEY (REELLINE__STEPS__PICK__MENU_SIZE=3; values read as YAML scalars),
     from the env file first, then the process environment
  4. explicit overrides from code or the CLI ({"steps.pick.menu_size": 3} or nested dicts)

The channel dir: load_config(channel_dir), else REELLINE_CHANNEL, else the one <repo>/channels/*/
with a config.yaml. Credentials never come through here: the config names their variables
(`*_env`) and reelline/config/credentials.py reads them from the env file when a feature needs one.

Readers call conf() (loaded once, cached); tests inject one with set_config().
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import ValidationError

from reelline.config.schema import Config
from reelline.core.errors import ConfigError
from reelline.paths import REPO_ROOT

CONFIG_FILE = "config.yaml"
CHANNEL_VAR = "REELLINE_CHANNEL"
EXAMPLE_CHANNEL = "example"
ENV_PREFIX = "REELLINE__"
ENV_FILE_VAR = "REELLINE__STORAGE__ENV_FILE"

log = logging.getLogger("reelline.config")
_config: Config | None = None


def channels_root() -> Path:
    return REPO_ROOT / "channels"


def default_channel_dir(environ: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    if environ.get(CHANNEL_VAR):
        return Path(environ[CHANNEL_VAR]).resolve()
    found = sorted(p.parent for p in channels_root().glob(f"*/{CONFIG_FILE}"))
    # The shipped example channel is a template, never the implicit one beside a real channel.
    found = [p for p in found if p.name != EXAMPLE_CHANNEL] or found
    if len(found) != 1:
        raise ConfigError(
            f"{len(found)} channels with a {CONFIG_FILE} under {channels_root()}: "
            f"name one with {CHANNEL_VAR} or --channel"
        )
    return found[0]


def _set(tree: dict[str, Any], dotted: str, value: Any) -> None:
    *parents, last = dotted.split(".")
    for key in parents:
        tree = tree.setdefault(key, {})
        if not isinstance(tree, dict):
            raise ConfigError(f"{dotted}: {key} is not a section")
    tree[last] = value


def _unshared(value: Any) -> Any:
    """A copy with no dict or list shared between two places: a YAML alias (*claude) gives every
    role the same dict, and a later layer's override of one role must not change the others."""
    if isinstance(value, dict):
        return {k: _unshared(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unshared(v) for v in value]
    return value


def _merge(base: dict[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in top.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _overrides_tree(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for key, value in (overrides or {}).items():
        if isinstance(value, Mapping) and "." not in key:
            _merge(tree.setdefault(key, {}), value)
        else:
            _set(tree, key, value)
    return tree


def _absolute_env_file(value: str, source: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ConfigError(f"{source} must be an absolute path, got: {value}")
    return path


def env_file_from(environ: Mapping[str, str]) -> Path | None:
    """The env file named in the process environment."""
    if environ.get(ENV_FILE_VAR):
        return _absolute_env_file(environ[ENV_FILE_VAR], ENV_FILE_VAR)
    return None


def default_env_file(channel_dir: Path) -> Path:
    """<channel>/.env when it exists, else <repo>/.env (where it lives on the server today)."""
    own = channel_dir / ".env"
    return own if own.is_file() else REPO_ROOT / ".env"


def _env_layer(values: Mapping[str, str | None], *, from_file: bool) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for name, raw in values.items():
        if raw is None:
            continue
        if name.startswith(ENV_PREFIX):
            dotted = name[len(ENV_PREFIX) :].lower().replace("__", ".")
            if from_file and dotted == "storage.env_file":
                continue
        else:
            continue
        value = raw.strip()
        if dotted == "storage.env_file":
            continue  # chosen before the layers are read (find_env_file)
        _set(tree, dotted, yaml.safe_load(value) if value else None)
    return tree


def load_config(
    channel_dir: Path | str | None = None,
    overrides: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    use_env: bool = True,
) -> Config:
    """The channel's config, every layer applied. use_env=False: defaults, config.yaml and the
    overrides only (no process environment, no env file)."""
    environ = (os.environ if environ is None else environ) if use_env else {}
    channel_dir = Path(channel_dir).resolve() if channel_dir else default_channel_dir(environ)
    path = channel_dir / CONFIG_FILE
    try:
        data = _unshared((yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None) or {})
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: not a mapping")
    data.setdefault("channel", {})
    if "dir" in data["channel"]:
        raise ConfigError(f"{path}: channel.dir is set by the loader, not in the file")

    top = _overrides_tree(overrides)
    if use_env:
        env_file = env_file_from(environ)
        if env_file is None:
            configured = (top.get("storage") or {}).get("env_file") or (data.get("storage") or {}).get("env_file")
            env_file = (channel_dir / configured) if configured else default_env_file(channel_dir)
        else:
            _set(data, "storage.env_file", str(env_file))
        if env_file.is_file():
            _merge(data, _env_layer(dotenv_values(env_file, interpolate=False), from_file=True))
        _merge(data, _env_layer(environ, from_file=False))
    _merge(data, top)
    data["channel"]["dir"] = str(channel_dir)
    data["channel"].setdefault("name", channel_dir.name)
    try:
        return Config.model_validate(data, context={"channel_dir": channel_dir})
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def conf() -> Config:
    """The loaded config (loaded on first use)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: Config | None) -> Config | None:
    """Make `config` the one conf() returns (None: load again on next use); returns the previous one."""
    global _config
    previous, _config = _config, config
    return previous


__all__ = ["Config", "ConfigError", "conf", "load_config", "set_config"]
