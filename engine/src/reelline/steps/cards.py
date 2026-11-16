"""cards: the end card's facts, written, fact-checked with one repair round of their own, a line
still failing left off (reelline/pipeline/endcard.py make_card())."""

from __future__ import annotations

from datetime import date

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps._script import publish_date, script_id, story_text


def run(
    brief: str,
    script_text: str,
    source_text: str,
    *,
    publish_date: date,
    writer=None,
    checker=None,
    conn=None,
    script_id: int | None = None,
    saved=None,
    log=print,
):
    """The card (endcard.Card: .lines, .dropped) for a script's display text."""
    from reelline.pipeline.endcard import make_card
    from reelline.providers import get_llm

    return make_card(
        conn,
        writer if writer is not None else get_llm("endcard"),
        checker if checker is not None else get_llm("checker"),
        brief=brief,
        script_text=script_text,
        source_text=source_text,
        publish_date=publish_date,
        script_id=script_id,
        saved=saved,
        log=log,
    )


class Cards(Step):
    name = "cards"
    key = "card"
    needs = frozenset({Artifact.STORY, Artifact.BRIEF, Artifact.SCRIPT})
    makes = frozenset({Artifact.CARDS})
    session = "script"

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.script import _today, display_text

        card = run(
            ctx[Artifact.BRIEF],
            display_text(ctx[Artifact.SCRIPT].script),
            story_text(ctx),
            publish_date=publish_date(ctx) or _today(),
            writer=ctx.llm("endcard"),
            checker=ctx.llm("checker"),
            conn=ctx.conn,
            script_id=script_id(ctx),
            saved=ctx.saved,
            log=ctx.log,
        )
        ctx.store.put(self.store_key, card.to_json())
        return StepResult({Artifact.CARDS: card})
