"""pick: the slot's story (reelline/pipeline/pick.py Picker: queued, ranked, maybe a menu)."""

from __future__ import annotations

from datetime import date

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


def pick_for_slot(
    conn, source_names: dict[str, str], slot_date: str, slot_time: str, *, menu: bool | None = None, log=print
) -> int | str | None:
    """The slot picked: a pick id, "menu" (a menu went out), or None (nothing eligible). menu
    None: a menu unless it's an evening-batch slot; False: never."""
    from reelline.pipeline import db
    from reelline.pipeline.pick import Picker
    from reelline.pipeline.run_daily import batch_slot

    if menu is None:
        menu = not batch_slot(slot_date)
    picker = Picker(conn, source_names=source_names, on_picked=lambda pick_id: None, log=log)
    picker.run(date.fromisoformat(slot_date), slot_time, menu=menu)
    pick = db.slot_pick(conn, slot_date, slot_time)
    if pick is not None:
        return pick["id"]
    if any((m["slot_date"], m["slot_time"]) == (slot_date, slot_time) for m in db.open_menus(conn)):
        return "menu"
    return None


class Pick(Step):
    name = "pick"
    needs = frozenset({Artifact.ASSESSMENTS})
    makes = frozenset({Artifact.STORY})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline import db

        conn = _db.conn(ctx, self.name)
        d, t = _db.slot(ctx, self.name)
        live = db.slot_pick(conn, d, t)
        found = (
            live["id"]
            if live is not None
            else pick_for_slot(
                conn, _db.names(ctx), d, t, menu=False if ctx.dry_run else ctx.state.get("menu"), log=ctx.log
            )
        )
        if found == "menu":
            return StepResult(status="wait", detail="menu sent")
        if found is None:
            return StepResult(status="wait", detail="nothing eligible")
        ctx.state["pick_id"] = found
        pick = conn.execute("SELECT * FROM picks WHERE id = ?", (found,)).fetchone()
        return StepResult({Artifact.STORY: db.get_story(conn, pick["story_id"])}, detail=f"pick #{found}")
