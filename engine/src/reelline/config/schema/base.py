"""Schema building blocks: Number, ChannelPath, env(), Section."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationInfo


def _channel_relative(value: Path, info: ValidationInfo) -> Path:
    base = (info.context or {}).get("channel_dir")
    return value if value.is_absolute() or base is None else Path(os.path.normpath(Path(base) / value))


Number = int | float
"""An int stays an int (it prints as one), a float a float."""

ChannelPath = Annotated[Path, AfterValidator(_channel_relative)]
"""A path, relative to the channel directory unless absolute."""


def env(default: str, what: str, *, secret: bool = True, optional: bool = False) -> Any:
    """A field naming an env file variable. `what` is the one-line comment of .env.example (what it
    is, where to get it); secret: its value is a credential (masked), else an account identifier;
    optional: a feature works (or is skipped) without it."""
    return Field(default, description=what, json_schema_extra={"env": True, "secret": secret, "optional": optional})


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True, revalidate_instances="always")
