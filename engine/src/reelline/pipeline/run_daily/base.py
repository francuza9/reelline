"""Constants and the clock helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from reelline.pipeline.scoring import tbilisi

CHAIN_KINDS = ("pick", "script_produce")
DIED = "the worker process died"  # approve_bot.WORKER_DIED


def utcnow() -> datetime:
    return datetime.now(UTC)


def hm(at: datetime) -> str:
    return at.astimezone(tbilisi()).strftime("%H:%M")
