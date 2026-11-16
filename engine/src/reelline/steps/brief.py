"""brief: the picked story's facts sheet (reelline/pipeline/brief.py build_brief()), once per pick."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


class Brief(Step):
    name = "brief"
    needs = frozenset({Artifact.STORY})
    makes = frozenset({Artifact.BRIEF})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline import db
        from reelline.pipeline.script import _brief_for, check_pick

        conn = _db.conn(ctx, self.name)
        pick = _db.pick(ctx, self.name)
        check_pick(pick, pick["id"])
        row = _brief_for(conn, pick, ctx.llm("briefer"), _db.names(ctx), ctx.log)
        ctx.state["pick_id"] = pick["id"]
        return StepResult({Artifact.STORY: db.get_story(conn, pick["story_id"]), Artifact.BRIEF: row["markdown"]})
