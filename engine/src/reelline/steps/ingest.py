"""ingest: every enabled source fetched, new stories stored (reelline/pipeline/ingest.py)."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


class Ingest(Step):
    name = "ingest"
    makes = frozenset({Artifact.STORIES})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline import ingest

        _db.conn(ctx, self.name)
        code = ingest.main([])
        if code != 0:
            return StepResult(status="failed", detail=f"ingest exited {code} (every source failed)")
        return StepResult({Artifact.STORIES: True})
