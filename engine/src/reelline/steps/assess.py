"""assess: the stories not assessed yet, scored for the slot's publish day (reelline/pipeline/assess.py).
pick assesses what's left itself, so a chain without this step behaves the same."""

from __future__ import annotations

from datetime import date

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


class Assess(Step):
    name = "assess"
    needs = frozenset({Artifact.STORIES})
    makes = frozenset({Artifact.ASSESSMENTS})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.assess import assess_stories

        conn = _db.conn(ctx, self.name)
        day = date.fromisoformat(_db.slot(ctx, self.name)[0])
        totals = assess_stories(conn, ctx.llm("assessor"), _db.names(ctx), publish_date=day, log=ctx.log)
        return StepResult({Artifact.ASSESSMENTS: totals}, detail=f"{totals['stories']} assessed")
