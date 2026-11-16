"""Channels, terminal states, the Outcome record and time helpers shared by the publish modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from reelline.config import conf
from reelline.publish.base import PublisherError
from reelline.storage.blob import StorageError

CHANNELS = tuple(conf().publish.targets)


BUFFER_CHANNELS = tuple(ch for ch, kind in conf().publish.targets.items() if kind == "buffer")


TERMINAL = ("published", "failed", "unscheduled")


CHANNEL_ERRORS = (PublisherError, StorageError)


def utcnow() -> datetime:
    return datetime.now(UTC)


def label(channel: str) -> str:
    return conf().publish.labels[channel]


def enabled_channels() -> list[str]:
    return [c for c in CHANNELS if conf().publish.channels.get(c)]


def local_hm(at: datetime) -> str:
    from reelline.pipeline.scoring import tbilisi

    return at.astimezone(tbilisi()).strftime("%H:%M")


def local_when(at: datetime, now: datetime) -> str:
    from reelline.pipeline.scoring import tbilisi

    day = at.astimezone(tbilisi()).date()
    return local_hm(at) + ("" if day == now.astimezone(tbilisi()).date() else f" ({day.isoformat()})")


@dataclass
class Outcome:
    reel_id: int
    results: dict = field(default_factory=dict)  # channel -> (state, error or None)
    warnings: list[str] = field(default_factory=list)
    when: datetime | None = None  # the slot, None for now
    credits_url: str | None = None
    links: list[str] = field(default_factory=list)  # the reel's shortlinks, checked live
    # A step every channel waits for (the guard, the credits page, the R2 upload) that
    # failed: its error, every channel's too. Reported once, with one Retry all.
    shared: str | None = None
