"""save: the script's files (the script, its end card and fx files, what the render reads), its
path recorded, the pick scripted (reelline/pipeline/script.py finish())."""

from __future__ import annotations

from reelline.pipeline.step import Artifact, Context, Step, StepResult
from reelline.steps import _db


class Save(Step):
    name = "save"
    needs = frozenset({Artifact.SCRIPT})
    wants = frozenset({Artifact.CARDS, Artifact.GRAPHICS, Artifact.CAPTION})
    makes = frozenset({Artifact.SCRIPT_FILE})

    def run(self, ctx: Context) -> StepResult:
        from reelline.pipeline.script import finish

        path = finish(_db.conn(ctx, self.name), _db.script_run(ctx, self.name))
        return StepResult({Artifact.SCRIPT_FILE: path})
