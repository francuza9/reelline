"""Produce's errors, the Produced result and small helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.paths import REPO_ROOT


class ProduceError(ReellineError, RuntimeError):
    """Nothing to produce, or a produce step that failed (the message says which)."""


class RenderFailed(ReellineError, RuntimeError):
    """A render attempt that failed (the renderer's own error, as text); paid_calls:
    the cost rows of what it spent before failing."""

    def __init__(self, message: str, paid_calls: list[dict] | None = None):
        super().__init__(message)
        self.paid_calls = list(paid_calls or [])


def attempts_error(errors: list[str]) -> str:
    """'attempt 1: <error> | attempt 2: <error>'; an attempt that failed exactly like the one
    before is 'same as attempt N' (a deterministic failure's stderr shows once)."""
    return " | ".join(
        f"attempt {i}: " + (f"same as attempt {i - 1}" if i > 1 and e == errors[i - 2] else e)
        for i, e in enumerate(errors, 1)
    )


def short_error(error: str) -> str:
    n = conf().steps.produce.error_chars
    return error if len(error) <= n else error[:n] + " [...]"


def rel_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


@dataclass
class Produced:
    reel_id: int
    status: str
    error: str | None = None
