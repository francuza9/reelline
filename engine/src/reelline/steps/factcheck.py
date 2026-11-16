"""factcheck: every claim of the script against the source (reelline/pipeline/factcheck.py), a
check: a failing draft sentence sends the work back to write (then style), a failing sentence style
added back to style; steps.factcheck.repair_rounds rounds."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps._script import script_id, story_text


def run(
    source_text: str,
    script,
    *,
    brief: str = "(no brief)",
    draft: str | None = None,
    llm=None,
    flirt: bool = False,
    conn=None,
    script_id: int | None = None,
):
    """The fact-check of a script (its text, or a parsed media.script_markup.Script) against the
    source: a factcheck.FactCheck (.passed, .failures, .claims). draft: the plain draft the script
    came from (its sentences are told apart from the ones styling added); None: the script's own."""
    from reelline.media.script_markup import parse
    from reelline.pipeline.factcheck import fact_check
    from reelline.pipeline.script import display_text, draft_lock
    from reelline.providers import get_llm

    parsed = parse(script, flirt=flirt) if isinstance(script, str) else script
    report = draft_lock(draft if draft is not None else display_text(parsed), parsed)
    return fact_check(conn, llm if llm is not None else get_llm("checker"), source_text, brief, report, script_id)


def rejects(fc) -> dict[str, list[str]]:
    """The failures by the artifact they came from: the draft's, style's (origin punchup)."""
    from reelline.pipeline.factcheck import failure_text

    out = {
        Artifact.DRAFT: [failure_text(c) for c in fc.failures if c["origin"] != "punchup"],
        Artifact.SCRIPT: [failure_text(c) for c in fc.failures if c["origin"] == "punchup"],
    }
    return {k: v for k, v in out.items() if v}


class Factcheck(Step):
    name = "factcheck"
    needs = frozenset({Artifact.STORY, Artifact.BRIEF, Artifact.DRAFT, Artifact.SCRIPT})
    makes = frozenset({Artifact.FACTCHECK, Artifact.DRAFT_LOCK})
    judges = frozenset({Artifact.DRAFT, Artifact.SCRIPT})
    session = "script"

    @property
    def repair_errors(self):
        from reelline.pipeline.factcheck import FactCheckError
        from reelline.pipeline.script import ScriptError

        return (ScriptError, FactCheckError)

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.factcheck import FactCheck, fact_check
        from reelline.pipeline.script import draft_lock

        draft, punch = ctx[Artifact.DRAFT], ctx[Artifact.SCRIPT]
        report = draft_lock(draft.text, punch.script)
        ctx.log(f"  draft lock: {report['counts']}")
        fc = ctx.saved(
            self.store_key,
            lambda: fact_check(
                ctx.conn, ctx.llm("checker"), story_text(ctx), ctx[Artifact.BRIEF], report, script_id(ctx)
            ),
            lambda f: f.to_json(),
            lambda d: FactCheck(d["claims"], d["inserted"]),
        )
        ctx.log(f"  fact-check: {fc.to_json()['counts']}")
        back = rejects(fc)
        note = None
        if Artifact.DRAFT in back:
            note = f"  {len(back[Artifact.DRAFT])} failure(s) from the draft: re-running story, then punch-up"
        elif back:
            note = f"  {len(back[Artifact.SCRIPT])} failure(s) from punch-up: re-running punch-up"
        return StepResult({Artifact.FACTCHECK: fc, Artifact.DRAFT_LOCK: report}, rejects=back, note=note)
