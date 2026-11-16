"""fx: the fact graphics' own fact-check; a failing one is left out of the render
(reelline/pipeline/fx.py check_fx())."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps._script import script_id, story_text


def run(source_text: str, brief: str, script, *, llm=None, conn=None, script_id: int | None = None, log=print):
    """The fx check (fx.FxCheck) of a parsed script's @fx values; no @fx: no call."""
    from reelline.pipeline.fx import check_fx
    from reelline.providers import get_llm

    return check_fx(
        conn, llm if llm is not None else get_llm("checker"), source_text, brief, script, script_id=script_id, log=log
    )


class Fx(Step):
    name = "fx"
    needs = frozenset({Artifact.STORY, Artifact.BRIEF, Artifact.SCRIPT})
    makes = frozenset({Artifact.GRAPHICS})
    session = "script"

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.fx import FxCheck

        fx = ctx.saved(
            self.store_key,
            lambda: run(
                story_text(ctx),
                ctx[Artifact.BRIEF],
                ctx[Artifact.SCRIPT].script,
                llm=ctx.llm("checker"),
                conn=ctx.conn,
                script_id=script_id(ctx),
                log=ctx.log,
            ),
            FxCheck.to_json,
            FxCheck.from_json,
        )
        return StepResult({Artifact.GRAPHICS: fx})
