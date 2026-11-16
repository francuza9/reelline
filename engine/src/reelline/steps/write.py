"""write: the story pass, brief -> plain draft (reelline/pipeline/script.py story_pass())."""

from __future__ import annotations

from datetime import date

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps._script import pass_json, publish_date, script_id


def run(
    brief: str,
    *,
    llm=None,
    publish_date: date | None = None,
    failures: list[str] | None = None,
    examples: str | None = "",
    conn=None,
    script_id: int | None = None,
):
    """The draft for a brief (a script.PassResult). examples: {{HOOK_EXAMPLES}} ("" leaves the
    section out; None: the channel's hook examples file)."""
    from reelline.pipeline.script import story_pass
    from reelline.providers import get_llm

    return story_pass(
        llm if llm is not None else get_llm("writer"),
        brief,
        publish_date=publish_date,
        conn=conn,
        script_id=script_id,
        failures=failures,
        examples=examples,
    )


class Write(Step):
    name = "write"
    key = "story"
    needs = frozenset({Artifact.BRIEF})
    makes = frozenset({Artifact.DRAFT})
    session = "script"

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.script import PassResult, openings_note

        draft = ctx.saved(
            self.store_key,
            lambda: run(
                ctx[Artifact.BRIEF],
                llm=ctx.llm("writer"),
                publish_date=publish_date(ctx),
                failures=ctx.failures.get(Artifact.DRAFT),
                examples=ctx.state.get("examples", ""),
                conn=ctx.conn,
                script_id=script_id(ctx),
            ),
            pass_json,
            lambda d: PassResult(d["text"], retried=d["retried"], openings=d.get("openings")),
        )
        ctx.log(
            f"  story ({ctx.route}): {len(draft.text.split())} words; {openings_note(draft.openings)}"
            + (f"; retried: {draft.retried}" if draft.retried else "")
        )
        return StepResult({Artifact.DRAFT: draft})
