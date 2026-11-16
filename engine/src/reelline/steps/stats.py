"""stats: the due snapshots of the posts' numbers (reelline/pipeline/stats.py tick(), through reelline/stats)."""

from __future__ import annotations

from datetime import UTC, datetime

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


class Stats(Step):
    name = "stats"
    needs = frozenset({Artifact.POST})
    makes = frozenset({Artifact.STATS})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline import stats

        done = stats.tick(_db.conn(ctx, self.name), ctx.state.get("now") or datetime.now(UTC), _db.say(ctx), ctx.log)
        return StepResult({Artifact.STATS: done}, status="ok" if done else "skipped", detail=done)
