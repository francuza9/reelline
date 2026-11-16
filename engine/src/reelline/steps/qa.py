"""qa: the reel's automatic checks (reelline/pipeline/qa.py run_flow() through produce.qa_part():
frames, audio, captions; a bad image is re-rolled and the reel rendered again)."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db
from reelline.steps.render import _review_factory


class Qa(Step):
    name = "qa"
    needs = frozenset({Artifact.VIDEO})
    makes = frozenset({Artifact.QA})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.produce import failed_alert, qa_part

        conn = _db.conn(ctx, self.name)
        o = ctx.state.get("produce", {})
        job = ctx[Artifact.VIDEO]
        failed = qa_part(conn, job, _db.names(ctx), o.get("checker_factory"), ctx.log)
        if failed is not None:
            ctx.state["produced"] = failed_alert(
                failed, failed.error, o.get("alert", True) and not ctx.dry_run, _review_factory(o), ctx.log
            )
            return StepResult(status="failed", detail=f"render failed: {failed.error}")
        reel = conn.execute("SELECT qa_status FROM reels WHERE id = ?", (job.reel_id,)).fetchone()
        return StepResult({Artifact.QA: reel["qa_status"]})
