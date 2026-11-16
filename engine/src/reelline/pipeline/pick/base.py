"""Pick's shared pieces: the error, the ranking record, small helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.pipeline.scoring import (
    Evaluation,
    tbilisi,
)
from reelline.pipeline.sources import load_sources
from reelline.providers import LLMUsage, cost_usd


def assess_cost(totals: dict) -> float:
    """The assess calls' cost for the log: totals' output already counts the thinking."""
    return cost_usd(conf().models.assessor.model, LLMUsage(totals["input"], totals["output"], 0)) or 0.0


class PickError(ReellineError, RuntimeError):
    """Bad CLI use or a state pick can't work with."""


def iso_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def opt_bool(value) -> bool | None:
    return None if value is None else bool(value)


@dataclass
class Ranking:
    slot_dt: datetime
    evaluations: list[Evaluation]  # every assessed story
    ranked: list[Evaluation]  # eligible, best first, one per story (dedupe)
    note: str | None = None


def nothing_eligible_text(label: str, evaluations: list[Evaluation], top: int = 3) -> str:
    """The alert when no story is eligible for a slot: how many were assessed and the
    most common reasons the recent ones (first seen within max_age_days) are out."""
    from collections import Counter

    recent = [
        e for e in evaluations if e.age_days is None or e.age_days <= conf().steps.pick.max_age_days
    ] or evaluations
    reasons = Counter(re.sub(r"\s*\(.*", "", g).split(":")[0] for e in recent for g in e.gates[:1])
    common = "; ".join(f"{why} ({n})" for why, n in reasons.most_common(top))
    return (
        f"⚠️ {conf().channel.shown_name()} pick {label}: no {conf().steps.pick.topic} opportunity passes the rules among {len(evaluations)} assessed "
        f"stories, so nothing is picked and nothing off-topic goes out."
        + (f" Most common reasons, recent stories: {common}." if common else "")
    )


def source_names(registry: Path) -> dict[str, str]:
    return {s.id: s.name for s in load_sources(registry)}


def today_tbilisi() -> date:
    return datetime.now(UTC).astimezone(tbilisi()).date()
