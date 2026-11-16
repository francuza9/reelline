"""review: the reel, its caption and the status block with the buttons sent to the review channel,
unless held for the evening delivery (reelline/pipeline/produce.py review_part()). The decision comes
back to the approval bot (reelline/pipeline/approve_bot.py), which queues publish."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db
from reelline.steps.render import _review_factory


class Review(Step):
    name = "review"
    needs = frozenset({Artifact.VIDEO})
    wants = frozenset({Artifact.QA})
    makes = frozenset({Artifact.REVIEW})
    sends = True

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.produce import review_part, skip_qa

        conn = _db.conn(ctx, self.name)
        d, t = _db.slot(ctx, self.name)
        o = ctx.state.get("produce", {})
        reel_id = ctx[Artifact.VIDEO].reel_id
        if Artifact.QA not in ctx:  # steps.qa off: the review says QA was skipped
            skip_qa(conn, reel_id, ctx.log)
        if o.get("send", True):
            review_part(conn, reel_id, d, t, _review_factory(o), o.get("clock"), ctx.log)
        return StepResult({Artifact.REVIEW: reel_id})
