"""FakeStats: a StatsCollector with scripted numbers (no network)."""

from __future__ import annotations

from reelline.stats.base import BaseCollector, Ctx, Followers, SourcePart, StatPub, empty


class FakeStats(BaseCollector):
    """`numbers`: native/external id -> METRICS fields to give (the rest None). `fail`: raise."""

    def __init__(
        self,
        name: str = "fake",
        source: str = "fake",
        channels: tuple[str, ...] = ("youtube",),
        numbers: dict[str, dict] | None = None,
        fail: Exception | None = None,
        followers: int | None = None,
    ):
        self.name, self.sources, self.channels = name, (source,), {source: channels}
        self.numbers, self.fail, self.follower_count = numbers or {}, fail, followers
        self.asked: list[list[str]] = []

    def collect(self, source: str, pubs: list[StatPub], ctx: Ctx):
        self.asked.append([str(p.external_id) for p in pubs])
        if self.fail:
            raise self.fail
        for p in pubs:
            if str(p.external_id) in self.numbers:
                yield (
                    p,
                    SourcePart(
                        {**empty(), **self.numbers[str(p.external_id)]}, {source: self.numbers[str(p.external_id)]}
                    ),
                )

    def follower_sources(self, ctx: Ctx) -> tuple[str, ...]:
        return self.sources if self.follower_count is not None else ()

    def followers(self, source: str, ctx: Ctx) -> Followers | None:
        return Followers(next(iter(self.channels[source])), self.follower_count, "fake")
