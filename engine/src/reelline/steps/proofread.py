"""proofread: grammar, spelling and phrasing on the script, taken only when the code's guards pass
(reelline/pipeline/proofread.py)."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps._script import flirt, script_id


def run(script_text: str, *, llm=None, conn=None, script_id: int | None = None, log=lambda m: print(m, flush=True)):
    """The proofread (proofread.Proofread: .applied, .text, .reason)."""
    from reelline.pipeline.proofread import proofread_script
    from reelline.providers import get_llm

    return proofread_script(
        llm if llm is not None else get_llm("proofreader"), script_text, conn=conn, script_id=script_id, log=log
    )


class Proofread(Step):
    name = "proofread"
    needs = frozenset({Artifact.SCRIPT})
    makes = frozenset({Artifact.SCRIPT})
    session = "script"

    def run(self, ctx: Context) -> StepResult:
        from reelline.media.script_markup import parse
        from reelline.pipeline.proofread import Proofread as Result
        from reelline.pipeline.proofread import save

        punch = ctx[Artifact.SCRIPT]

        def proofread():
            pr = run(punch.text, llm=ctx.llm("proofreader"), conn=ctx.conn, script_id=script_id(ctx), log=ctx.log)
            if ctx.conn is not None and script_id(ctx) is not None:
                save(ctx.conn, script_id(ctx), pr)
            return pr

        pr = ctx.saved(
            self.store_key,
            proofread,
            lambda p: {"applied": p.applied, "text": p.text},
            lambda d: Result("script", punch.text, d["text"], d["applied"]),
        )
        if pr.applied:
            punch.text, punch.script = pr.text, parse(pr.text, flirt=flirt(ctx))
        return StepResult({Artifact.SCRIPT: punch})
