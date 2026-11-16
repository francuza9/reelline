"""caption: the post's caption fields, unless the script has them (reelline/pipeline/caption.py)."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


class Caption(Step):
    name = "caption"
    needs = frozenset({Artifact.SCRIPT})
    makes = frozenset({Artifact.CAPTION})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.caption import write_caption

        conn = _db.conn(ctx, self.name)
        script_id = _db.script_run(ctx, self.name).script_id
        if conn.execute("SELECT 1 FROM captions WHERE script_id = ?", (script_id,)).fetchone() is None:
            write_caption(conn, ctx.llm("caption"), script_id)
        return StepResult({Artifact.CAPTION: script_id})
