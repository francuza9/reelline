"""render: the saved script rendered to a reel and its cover, the voice and images included
(reelline/pipeline/produce.py render_part(); the renderer: scripts/render_reel.py through
reelline/render). Makes the reel row and its caption; a failed render ends the run."""

from __future__ import annotations

from pathlib import Path

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


def run(script_path: Path | str, *, background: str, mood: str | None = None, renderer=None, **options):
    """A script file rendered (produce.Rendered: .mp4, .cover, .duration), no database:
    renderer(script_path, background, mood, regen, **options), the real one by default."""
    from reelline.config import conf
    from reelline.pipeline.produce import render_reel

    return (renderer or render_reel)(
        Path(script_path), background, mood or conf().steps.produce.default_mood, [], **options
    )


class Render(Step):
    name = "render"
    needs = frozenset({Artifact.SCRIPT_FILE})
    makes = frozenset({Artifact.AUDIO, Artifact.VIDEO})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.produce import failed_alert, render_part

        conn = _db.conn(ctx, self.name)
        d, t = _db.slot(ctx, self.name)
        o = ctx.state.get("produce", {})
        job = render_part(
            conn,
            d,
            t,
            _db.names(ctx),
            background=o.get("background"),
            **({"renderer": o["renderer"]} if "renderer" in o else {}),
            log=ctx.log,
            keep_background=o.get("keep_background"),
            render_options=o.get("render_options"),
        )
        if isinstance(job, tuple):
            produced = failed_alert(*job, o.get("alert", True) and not ctx.dry_run, _review_factory(o), ctx.log)
            ctx.state["produced"] = produced
            return StepResult(status="failed", detail=f"render failed: {produced.error}")
        return StepResult({Artifact.AUDIO: job.rendered, Artifact.VIDEO: job}, detail=f"reel #{job.reel_id}")


def _review_factory(o: dict):
    from reelline.pipeline.produce import Review

    return o.get("review_factory", Review)
