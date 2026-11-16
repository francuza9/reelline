"""What the database-mode steps share: the slot, its pick, the run's options (ctx.state)."""

from __future__ import annotations

from reelline.pipeline.step import Context, StepError


def conn(ctx: Context, step: str):
    if ctx.conn is None:
        raise StepError(f"{step} needs the channel's database (run it with a channel, not on plain inputs)")
    return ctx.conn


def slot(ctx: Context, step: str) -> tuple[str, str]:
    if "slot" not in ctx.state:
        raise StepError(f"{step} needs a slot (--slot HH:MM [--date YYYY-MM-DD])")
    return ctx.state["slot"]


def pick(ctx: Context, step: str):
    """The run's pick: state pick_id, else the slot's live pick."""
    from reelline.pipeline import db

    c = conn(ctx, step)
    if ctx.state.get("pick_id") is not None:
        return c.execute("SELECT * FROM picks WHERE id = ?", (ctx.state["pick_id"],)).fetchone()
    row = db.slot_pick(c, *slot(ctx, step))
    if row is None:
        raise StepError(f"{step}: no pick for {' '.join(slot(ctx, step))}")
    return row


def names(ctx: Context) -> dict[str, str]:
    if "source_names" not in ctx.state:
        from reelline.pipeline.sources import load_sources

        ctx.state["source_names"] = {s.id: s.name for s in load_sources()}
    return ctx.state["source_names"]


def say(ctx: Context):
    """Where a step's lines go: the notifier, the log in a dry run."""
    if ctx.dry_run:
        return ctx.log
    if "say" not in ctx.state:
        from reelline.notify import get_notifier

        notifier = get_notifier()
        ctx.state["say"] = lambda text: notifier.send(text)
    return ctx.state["say"]


def script_run(ctx: Context, step: str):
    """The script session's ScriptRun, or the pick's stored script (a run from after the session)."""
    if "script_run" not in ctx.state:
        from reelline.pipeline.script import stored_run

        ctx.state["script_run"] = stored_run(conn(ctx, step), pick(ctx, step)["id"])
    return ctx.state["script_run"]
