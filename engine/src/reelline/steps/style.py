"""style: the second pass, draft -> the styled, parsed script (reelline/pipeline/script.py
punchup_pass(): punch-up, or straight mode's pass, reelline/media/style.py)."""

from __future__ import annotations

from datetime import date

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps._script import flirt, pass_json, publish_date, script_id


def run(
    brief: str,
    draft: str,
    *,
    llm=None,
    publish_date: date | None = None,
    failures: list[str] | None = None,
    flirt: bool = False,
    angle: str | None = None,
    conn=None,
    script_id: int | None = None,
    previous: str | None = None,
    log=lambda m: print(m, flush=True),
):
    """The script for a draft (a script.PassResult with .script). conn: the recent lines, flirts,
    details and shares it must not repeat come from the database; None: none yet."""
    from reelline.media import style as reel_style
    from reelline.pipeline import script as s
    from reelline.providers import get_llm

    jokes = reel_style.current().jokes
    return s.punchup_pass(
        llm if llm is not None else get_llm("stylist"),
        brief,
        draft,
        (s.recent_lines(conn) if conn is not None else s.NO_RECENT_LINES) if jokes else s.NO_RECENT_LINES,
        previous=previous,
        publish_date=publish_date,
        conn=conn,
        script_id=script_id,
        failures=failures,
        flirt=flirt,
        recent_flirt_lines=s.recent_flirt_lines(conn) if conn is not None else s.NO_RECENT_FLIRTS,
        flirt_angle=angle,
        banned_ok=s.banned_phrase_ok(conn) if conn is not None else True,
        recent_details_lines=s.recent_details(conn) if conn is not None else (),
        recent_share_lines=s.recent_shares(conn) if conn is not None else (),
        log=log,
    )


class Style(Step):
    name = "style"
    key = "punchup"
    needs = frozenset({Artifact.BRIEF, Artifact.DRAFT})
    makes = frozenset({Artifact.SCRIPT})
    session = "script"

    def run(self, ctx: Context) -> StepResult:
        from reelline.media.script_markup import parse
        from reelline.pipeline.script import PassResult

        draft = ctx[Artifact.DRAFT]
        punch = ctx.saved(
            self.store_key,
            lambda: run(
                ctx[Artifact.BRIEF],
                draft.text,
                llm=ctx.llm("stylist"),
                publish_date=publish_date(ctx),
                failures=ctx.failures.get(Artifact.SCRIPT) or None,
                flirt=flirt(ctx),
                angle=ctx.state.get("angle"),
                conn=ctx.conn,
                script_id=script_id(ctx),
                previous=ctx.state.get("previous"),
                log=ctx.log,
            ),
            pass_json,
            lambda d: PassResult(
                d["text"],
                script=parse(d["text"], flirt=flirt(ctx)),
                retried=d["retried"],
                fx_repaired=d.get("fx_repaired"),
            ),
        )
        ctx.log(
            f"  punch-up ({ctx.route}): parsed"
            + (f" after a retry ({punch.retried})" if punch.retried else "")
            + (f"; fact cards repaired in code: {'; '.join(punch.fx_repaired)}" if punch.fx_repaired else "")
        )
        return StepResult({Artifact.SCRIPT: punch})
