"""publish: an approved reel's posts created on every target (reelline/pipeline/publish.py
publish_reel(), through reelline/publish). In the daily run the approval bot's publish job does it
after the review's Approve; in a chain run it waits for that."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


class Publish(Step):
    name = "publish"
    needs = frozenset({Artifact.REVIEW})
    makes = frozenset({Artifact.POST})
    sends = True

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline import publish

        conn = _db.conn(ctx, self.name)
        reel_id = ctx[Artifact.REVIEW]
        reel = conn.execute("SELECT status FROM reels WHERE id = ?", (reel_id,)).fetchone()
        if reel is None or reel["status"] not in ("approved", "partially_published"):
            return StepResult(status="wait", detail=f"reel #{reel_id} waits for its review's decision")
        clients = ctx.state.get("publish_clients") or publish.real_clients(conn, reel_id)
        out = publish.publish_reel(conn, reel_id, clients, log=ctx.log)
        return StepResult({Artifact.POST: out}, detail=f"{len(out.results)} channel(s)")
