"""rank: what worked, from the stats: each reel's retention line after +72 h, and the week's hook
examples (winners and flops) the write step shows the model (reelline/pipeline/retention.py tick())."""

from __future__ import annotations

from datetime import UTC, datetime

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


class Rank(Step):
    name = "rank"
    needs = frozenset({Artifact.STATS})
    makes = frozenset({Artifact.RANKING})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline import retention

        done = retention.tick(
            _db.conn(ctx, self.name), ctx.state.get("now") or datetime.now(UTC), _db.say(ctx), ctx.log
        )
        return StepResult({Artifact.RANKING: done}, status="ok" if done else "skipped", detail=done)
