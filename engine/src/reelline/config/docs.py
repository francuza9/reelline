"""The configuration reference (docs/configuration.md), generated from the schema: `reelline config docs`.

One section per schema class, in the order of the config; a row per setting with its type, its default and the
comment written above it in the schema. Model roles are described once (every role has the same keys) and their
per-role defaults are left out, as are the price table's entries: a channel sets its own.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from reelline.config.schema import Config
from reelline.config.schema.models import ImageRole, ModelRole, TtsRole

MAX_DEFAULT = 60
# A default naming a vendor's model or product is not shown: models and their prices are the channel's choice.
_VENDOR = re.compile(r"claude|gemini|anthropic", re.IGNORECASE)
_ROLE_TYPES = (ModelRole, ImageRole, TtsRole)


def _comments(cls: type) -> dict[str, str]:
    """{field name: the `#` comment lines directly above it in the class's source}."""
    try:
        lines, _ = inspect.getsourcelines(cls)
    except (OSError, TypeError):
        return {}
    source = textwrap.dedent("".join(lines))
    src_lines = source.splitlines()
    out: dict[str, str] = {}
    for node in ast.parse(source).body[0].body:  # type: ignore[attr-defined]
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        above: list[str] = []
        i = node.lineno - 2
        while i >= 0 and src_lines[i].strip().startswith("#"):
            above.insert(0, src_lines[i].strip().lstrip("#").strip())
            i -= 1
        if above:
            out[node.target.id] = " ".join(above)
    return out


def _type(annotation: Any) -> str:
    if annotation is None or annotation is type(None):
        return "none"
    if hasattr(annotation, "__metadata__"):  # Annotated[X, ...]
        return _type(annotation.__origin__)
    origin = get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation)).replace("PosixPath", "path")
    args = get_args(annotation)
    if str(origin) == "typing.Literal":
        return " \\| ".join(repr(a) for a in args)
    if str(origin) in ("typing.Annotated",):
        return _type(args[0])
    name = getattr(origin, "__name__", str(origin))
    if name in ("UnionType", "Union"):
        return " \\| ".join(_type(a) for a in args)
    if args and args[-1] is Ellipsis:
        return f"{name}[{_type(args[0])}, ...]"
    return f"{name}[{', '.join(_type(a) for a in args)}]" if args else name


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {k: _plain(getattr(value, k)) for k in type(value).model_fields}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(_plain(v) for v in value)  # hash order differs per run
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return str(value) if not isinstance(value, str | int | float | bool | type(None)) else value


def _default(value: Any, hide: bool = False, vendor_ok: bool = False) -> str:
    if hide:
        return "per channel"
    if value is PydanticUndefined:
        return "required"
    shown = repr(_plain(value)).replace("|", "\\|")
    if not vendor_ok and _VENDOR.search(shown):
        return "see the schema"
    return shown if len(shown) <= MAX_DEFAULT else shown[: MAX_DEFAULT - 1] + "…"


def _is_role(model: BaseModel) -> bool:
    return isinstance(model, _ROLE_TYPES)


def _section(path: str, model: BaseModel | type[BaseModel], blocks: list[str]) -> None:
    cls = model if isinstance(model, type) else type(model)
    instance = None if isinstance(model, type) else model
    doc = inspect.cleandoc(cls.__dict__["__doc__"]) if cls.__dict__.get("__doc__") else ""
    comments = _comments(cls)
    rows, children = [], []
    for name, field in cls.model_fields.items():
        value = getattr(instance, name) if instance is not None else field.get_default(call_default_factory=True)
        if isinstance(value, BaseModel):
            children.append((f"{path}.{name}" if path else name, value))
            continue
        hide = path == "models" and name == "prices"
        text = (comments.get(name) or field.description or "").replace("Gemini TTS", "TTS")
        if field.json_schema_extra and isinstance(field.json_schema_extra, dict) and field.json_schema_extra.get("env"):
            text = f"(env variable name) {text}".strip()
        rows.append(
            f"| `{name}` | {_type(field.annotation)} | {_default(value, hide, path.startswith('models.providers'))} | {text.replace('|', chr(92) + '|')} |"
        )
    out = [f"### `{path}`" if path else "### top level", ""]
    if doc:
        out += [doc, ""]
    if rows:
        out += ["| Key | Type | Default | What it does |", "|---|---|---|---|", *rows, ""]
    blocks.append("\n".join(out))
    roles_done = False
    for child_path, child in children:
        if _is_role(child):
            if not roles_done and path == "models":
                roles = [p.rsplit(".", 1)[1] for p, c in children if type(c) is ModelRole]
                blocks.append(
                    "\n".join(
                        [
                            f"Roles: {', '.join(f'`{r}`' for r in roles)}, and the extra kinds `illustrator`"
                            " (an image role) and `tts` (a voice role) below. Each role's provider, model and"
                            " parameters are the channel's to set; every role has the keys of `models.<role>`.",
                            "",
                        ]
                    )
                )
                for kind, label in (
                    (ModelRole, "models.<role>"),
                    (ImageRole, "models.illustrator"),
                    (TtsRole, "models.tts"),
                ):
                    _section(label, kind, blocks)
                roles_done = True
            continue
        _section(child_path, child, blocks)


def render() -> str:
    head = [
        "# Configuration reference",
        "",
        "Generated by `reelline config docs` from the schema (`engine/src/reelline/config/schema/`); do not edit by hand.",
        "A channel's `config.yaml` sets what differs from these defaults. Paths are relative to the channel directory.",
        "Credentials are never in the config: a field marked *(env variable name)* names the variable in the channel's"
        " `.env` that holds the value. Long defaults are cut with `…`; `reelline config show` prints the merged config"
        " in full. Any setting can also be overridden for one run with `--set section.key=value`.",
        "",
    ]
    blocks: list[str] = []
    _section("", Config(), blocks)
    return "\n".join(head) + "\n" + "\n".join(blocks).rstrip() + "\n"
