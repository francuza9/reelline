"""The script session's pass store and steps."""

from __future__ import annotations

import json
import sqlite3

from reelline.pipeline.script.lock import draft_lock
from reelline.pipeline.script.runs import Version, _update

# The roles write_script()'s `claude` serves (the script session's steps ask ctx.llm(role)); "checker" is `gemini`.
CLAUDE_ROLES = ("writer", "stylist", "proofreader", "endcard", "caption")


class PassesStore:
    """The script session's ResultStore (reelline/pipeline/step.py): scripts.passes, written at once."""

    def __init__(self, conn: sqlite3.Connection, script_id: int, passes: dict):
        self.conn, self.script_id, self.passes = conn, script_id, passes

    def __contains__(self, key: str) -> bool:
        return key in self.passes

    def get(self, key: str):
        return self.passes[key]

    def put(self, key: str, value) -> None:
        self.passes[key] = value
        _update(self.conn, self.script_id, passes=json.dumps(self.passes, ensure_ascii=False))


def session_steps() -> list:
    """The configured chain's script session steps (write ... fx), in order."""
    from reelline.pipeline.chain import load_chain

    return [s for s in load_chain().steps if s.session == "script"]


def _versions(ctx) -> list[Version]:
    """The rounds that ran to the end, so far (a step's error is being handled)."""
    from reelline.pipeline.step import Artifact

    return [
        Version(
            r.route,
            r.artifacts[Artifact.DRAFT],
            r.artifacts[Artifact.SCRIPT],
            r.artifacts[Artifact.DRAFT_LOCK],
            r.artifacts[Artifact.FACTCHECK],
        )
        for r in ctx.state.get("rounds", [])
    ]


def unchecked(ctx) -> Version:
    """The one version of a chain without a fact-check (steps.factcheck.enabled false): nothing failed."""
    from reelline.pipeline.factcheck import FactCheck
    from reelline.pipeline.step import Artifact

    draft, punch = ctx[Artifact.DRAFT], ctx[Artifact.SCRIPT]
    return Version("first", draft, punch, draft_lock(draft.text, punch.script), FactCheck())


# ---- Files, CLI ------------------------------------------------------------------------
