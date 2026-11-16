"""`reelline init <dir>`: a new channel from the shipped example (channels/example): its config, prompts,
sources, host and background copied, the name filled in, and a .env.example generated for it."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from reelline.core.errors import ConfigError
from reelline.paths import REPO_ROOT

TEMPLATE = REPO_ROOT / "channels" / "example"
NAME = re.compile(r"[a-z][a-z0-9_-]*")
# Never copied: what a run of the template wrote (its database, scripts, renders) and its credentials.
SKIP = shutil.ignore_patterns("data", "content", "out", "logs", ".env", "__pycache__", "*.db")
TEXT = (".yaml", ".yml", ".md", ".json", ".txt", ".html")


def titled(name: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", name))


def scaffold(dest: Path, name: str | None = None, template: Path = TEMPLATE) -> Path:
    """The channel `name` (default: the directory's name) in `dest`, copied from the template; ConfigError for a
    name that isn't lowercase letters, digits, - and _, a missing template or a non-empty directory."""
    from reelline.cli import env_example
    from reelline.config import load_config

    dest = Path(dest)
    name = name or dest.resolve().name
    if not NAME.fullmatch(name):
        raise ConfigError(f"channel name {name!r}: lowercase letters, digits, - and _, starting with a letter")
    if not (template / "config.yaml").is_file():
        raise ConfigError(f"no channel template at {template}")
    if dest.exists() and any(dest.iterdir()):
        raise ConfigError(f"{dest} exists and isn't empty")
    shutil.copytree(template, dest, ignore=SKIP, dirs_exist_ok=True)
    (dest / ".env.example").unlink(missing_ok=True)
    display, env = titled(name), name.upper().replace("-", "_")
    swaps = [
        ("Example Explainer", display),
        ("ExampleExplainer", display.replace(" ", "")),
        ("EXAMPLE_VOICE_ID", f"{env}_VOICE_ID"),
        ("example-publisher", f"{name}-publisher"),
        ("example-host", f"{name}-host"),
        ("#example", f"#{name}"),
        ("name: example\n", f"name: {name}\n"),
        ("example explainer", display.lower()),
    ]
    for path in dest.rglob("*"):
        if path.is_file() and path.suffix in TEXT:
            text = original = path.read_text(encoding="utf-8")
            for old, new in swaps:
                text = text.replace(old, new)
            if text != original:
                path.write_text(text, encoding="utf-8")
    (dest / ".env.example").write_text(env_example(load_config(dest, use_env=False)), encoding="utf-8")
    return dest
