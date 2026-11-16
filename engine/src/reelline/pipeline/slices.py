"""The chain run on the channel's database: the slices the daily jobs run (reelline/pipeline/approve_bot.py
Steps: the script job brief ... save, the produce job render ... review) and `reelline run`.

The script session (write ... fx) runs through script.write_script(), which keeps the scripts row
and its passes; a slice cutting into it gets all of it (finished passes are kept, not run again).
"""

from __future__ import annotations

from collections.abc import Callable

from reelline.pipeline.chain import Chain, load_chain
from reelline.pipeline.runner import Runner, RunOutcome
from reelline.pipeline.step import Artifact, Context, Step, StepResult


def db_context(
    conn,
    slot_date: str,
    slot_time: str,
    *,
    source_names: dict[str, str] | None = None,
    log: Callable[[str], None] = print,
    dry_run: bool = False,
    clients: dict | None = None,
    pick_id: int | None = None,
    produce: dict | None = None,
) -> Context:
    state = {"slot": (slot_date, slot_time), "produce": dict(produce or {})}
    if source_names is not None:
        state["source_names"] = source_names
    if pick_id is not None:
        state["pick_id"] = pick_id
    ctx = Context(conn=conn, log=log, dry_run=dry_run, clients=dict(clients or {}), state=state)
    ctx.sessions["script"] = script_session
    return ctx


def script_session(steps: list[Step], ctx: Context) -> StepResult:
    """The script session's steps over the pick's script row (write_script())."""
    from reelline.pipeline.script import write_script
    from reelline.steps import _db

    run = write_script(
        ctx.conn,
        _db.pick(ctx, "write")["id"],
        claude=ctx.llm("writer"),
        gemini=ctx.llm("checker"),
        source_names=_db.names(ctx),
        log=ctx.log,
        steps=steps,
    )
    ctx.state["script_run"] = run
    final = run.final
    made = {
        Artifact.DRAFT: final.story,
        Artifact.SCRIPT: final.punchup,
        Artifact.DRAFT_LOCK: final.report,
        Artifact.FACTCHECK: final.factcheck,
        Artifact.CARDS: run.card,
        Artifact.GRAPHICS: run.fx,
    }
    return StepResult({k: v for k, v in made.items() if v is not None}, detail=f"script #{run.script_id} {run.status}")


def whole_sessions(steps: list[Step], chain: Chain) -> list[Step]:
    """steps, with every session they cut into whole."""
    sessions = {s.session for s in steps if s.session}
    if not sessions:
        return steps
    names = {s.name for s in steps} | {s.name for s in chain.steps if s.session in sessions}
    return [s for s in chain.steps if s.name in names]


def script_clients() -> dict:
    """The script job's providers, as it always had them: one writer connection (story, punch-up,
    proofread, card, caption), one checker (brief, fact-checks)."""
    from reelline.providers import get_llm

    claude, gemini = get_llm("writer"), get_llm("checker")
    return {"writer": claude, "caption": claude, "checker": gemini, "briefer": gemini}


def run_script(
    conn,
    pick_id: int,
    *,
    source_names: dict[str, str],
    log: Callable[[str], None] = print,
    chain: Chain | None = None,
    clients: dict | None = None,
) -> int:
    """The script job: the steps after the pick through the saved files (brief ... save). Returns
    the script id; a step's error is raised (script.ScriptError for a failed script)."""
    from reelline.pipeline.script import ScriptError

    pick = conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone()
    ctx = db_context(
        conn,
        pick["slot_date"],
        pick["slot_time"],
        source_names=source_names,
        log=log,
        clients=clients if clients is not None else script_clients(),
        pick_id=pick_id,
    )
    chain = chain or load_chain()
    outcome = Runner(whole_sessions(chain.between(Artifact.STORY, Artifact.SCRIPT_FILE), chain)).run(ctx)
    if outcome.status != "ok":
        raise ScriptError(f"pick #{pick_id}: {outcome.stopped_at} {outcome.status}: {outcome.detail}")
    return ctx.state["script_run"].script_id


def run_produce(
    conn,
    slot_date: str,
    slot_time: str,
    source_names: dict[str, str],
    *,
    log: Callable[[str], None] = print,
    chain: Chain | None = None,
    **options,
):
    """The produce job: render ... review for the slot's saved script (produce.produce()'s parts),
    every paid call's cost row written the moment it returns. options: produce()'s (keep_background,
    render_options, renderer, review_factory, checker_factory, clock, alert). Returns produce.Produced."""
    from reelline.pipeline import db
    from reelline.pipeline.produce import Produced, latest_script, slot_pick
    from reelline.providers.costs import recording

    script = latest_script(conn, slot_pick(conn, slot_date, slot_time)["id"])
    ctx = db_context(conn, slot_date, slot_time, source_names=source_names, log=log, produce=options)
    chain = chain or load_chain()
    with recording(db.live_recorder(conn, script_id=script["id"])):
        outcome = Runner(chain.between(Artifact.SCRIPT_FILE, Artifact.REVIEW)).run(ctx)
    if outcome.status == "failed":
        return ctx.state["produced"]
    return Produced(ctx[Artifact.VIDEO].reel_id, "rendered")


def run_chain(
    conn,
    slot_date: str,
    slot_time: str,
    *,
    start: str | None = None,
    stop: str | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
    chain: Chain | None = None,
    **context,
) -> RunOutcome:
    """`reelline run`: the chain (or --from/--to) for one slot, on the database."""
    chain = chain or load_chain()
    steps = whole_sessions(chain.slice(start, stop), chain)
    ctx = db_context(conn, slot_date, slot_time, log=log, dry_run=dry_run, **context)
    outcome = Runner(steps).run(ctx)
    log(
        f"{slot_date} {slot_time}: {outcome.status}"
        + (f" at {outcome.stopped_at}: {outcome.detail}" if outcome.stopped_at else "")
        + (f"; skipped: {', '.join(outcome.skipped)}" if outcome.skipped else "")
    )
    return outcome
