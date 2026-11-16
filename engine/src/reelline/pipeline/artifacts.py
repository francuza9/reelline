"""Artifacts as plain data: what `reelline run|step --input FILE` reads, and how a result is printed.

An input file is JSON: {"Story": "<source text>" | {"text": ...}, "Brief": "<markdown>",
"Draft": "<plain draft>", "Script": "<script in the markup>", ...}; with "flirt": true when the
script has its @flirt line. Anything else is passed on as it is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reelline.pipeline.step import Artifact


def load_inputs(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """(artifacts, state) from an input file."""
    from reelline.media.script_markup import parse
    from reelline.pipeline.script import PassResult

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    state = {"flirt": bool(data.pop("flirt", False))}
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == Artifact.STORY and isinstance(value, str):
            value = {"text": value}
        elif key == Artifact.DRAFT and isinstance(value, str):
            value = PassResult(value)
        elif key == Artifact.SCRIPT and isinstance(value, str):
            value = PassResult(value, script=parse(value, flirt=state["flirt"]))
        out[key] = value
    return out, state


def plain(value: Any) -> Any:
    """A made artifact as JSON-able data."""
    if hasattr(value, "to_json"):
        return value.to_json()
    if hasattr(value, "text") and isinstance(value.text, str):
        return value.text
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)


def dump(made: dict[str, Any]) -> str:
    return json.dumps({str(k): plain(v) for k, v in made.items()}, ensure_ascii=False, indent=1)
