"""steps.analytics.collectors -> the StatsCollectors of a round: get_collectors(apis).

`apis` is the container of API clients (reelline/pipeline/stats.py Apis: buffer, meta, youtube,
tiktok; None for a platform whose credentials are missing, which its collector reports as off).
"""

from __future__ import annotations

from typing import Any

from reelline.config import conf
from reelline.stats.base import StatsCollector


def _make(name: str, apis: Any) -> StatsCollector:
    if name == "buffer":
        from reelline.stats.buffer import BufferStats

        return BufferStats(apis.buffer)
    if name == "meta":
        from reelline.stats.meta import MetaCollector

        return MetaCollector(apis.meta)
    if name == "youtube":
        from reelline.stats.youtube import YouTubeStats

        return YouTubeStats(apis.youtube, apis.youtube_settings)
    if name == "tiktok":
        from reelline.stats.tiktok import TikTokStats

        return TikTokStats(getattr(apis, "tiktok", None))
    raise ValueError(f"no stats collector {name!r} (steps.analytics.collectors: buffer, meta, youtube, tiktok)")


def get_collectors(apis: Any, names: tuple[str, ...] | None = None) -> list[StatsCollector]:
    """The configured collectors in order (the order sources are fetched and merged)."""
    out = [_make(n, apis) for n in (names or conf().steps.analytics.collectors)]
    out += list(getattr(apis, "extra", None) or [])  # collectors handed in as they are (tests, plugins)
    return out
