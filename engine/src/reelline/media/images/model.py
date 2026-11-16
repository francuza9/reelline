"""Image errors, the evaluation and pick records."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from reelline.core.errors import ReellineError

MANIFEST_VERSION = 1


class ImageError(ReellineError, RuntimeError):
    """Raised for a bad pin, a missing library file, or a broken manifest."""


class CostCapReached(ReellineError, RuntimeError):
    """The next paid call would pass the picker's cost cap."""


class SlotBudgetReached(CostCapReached):
    """The next paid call would pass the extra pictures' vision budget (CANDIDATES)."""


@dataclass
class Evaluation:
    source: str
    title: str
    status: str  # "pass" | "fail" | "rejected" (never reached the vision check this time)
    reasons: list[str] = field(default_factory=list)
    license: str | None = None
    image_id: str | None = None
    record: dict | None = None  # library row (built but not stored in a dry run)
    result: dict | None = None  # vision result
    path: Path | None = None
    cached: bool = False  # vision result came from the cache

    @property
    def relevance(self) -> int | None:
        return self.result["relevance"] if self.result else None

    @property
    def paid(self) -> bool:
        """A vision call was made for this evaluation."""
        return self.result is not None and not self.cached


@dataclass
class Pick:
    chosen: Evaluation | None
    evaluations: list[Evaluation]
    generated: bool  # the generated fallback was tried
    reason: str  # why there's no image ("" when chosen)


def no_pick_reason(evals: list[Evaluation], tail: str) -> str:
    if not evals:
        return "no candidates found" + tail
    # First reason per candidate, without its details ("relevance 3 < 6" -> "relevance").
    counts = Counter(e.reasons[0].split(":")[0].split(" (")[0].split(" <")[0] for e in evals if e.reasons)
    top = ", ".join(f"{reason} x{n}" for reason, n in counts.most_common(3))
    checked = sum(1 for e in evals if e.result is not None)
    paid = sum(e.paid for e in evals)
    return f"no passing candidate ({len(evals)} candidates, {checked} vision-checked, {paid} paid: {top}){tail}"
