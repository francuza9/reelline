"""Filling the script prompts' {{NAME}} placeholders.

fill() replaces every placeholder in one pass (so a value that itself contains
"{{...}}" is never filled again) and fails loudly for a placeholder with no value,
a value with no placeholder, or an empty value.
"""

from __future__ import annotations

import re
from pathlib import Path

from reelline.core.errors import ReellineError

PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")


class PromptError(ReellineError, ValueError):
    """Raised for a prompt template and values that don't fit together."""


def fill(template: str, values: dict[str, str], name: str = "the prompt") -> str:
    found = PLACEHOLDER.findall(template)
    missing = sorted(set(found) - set(values))
    if missing:
        raise PromptError(f"Unfilled placeholder(s) in {name}: " + ", ".join(f"{{{{{m}}}}}" for m in missing))
    unused = sorted(set(values) - set(found))
    if unused:
        raise PromptError(f"{name} has no placeholder for: " + ", ".join(f"{{{{{u}}}}}" for u in unused))
    empty = sorted(key for key, value in values.items() if not value.strip())
    if empty:
        raise PromptError("Empty value for: " + ", ".join(f"{{{{{e}}}}}" for e in empty))
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def read_value(path: Path) -> str:
    """A placeholder value from a UTF-8 file, without trailing whitespace."""
    if not path.is_file():
        raise PromptError(f"File not found: {path}")
    return path.read_text(encoding="utf-8").rstrip()
