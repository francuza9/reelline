"""What the script session's steps share (write, style, proofread, factcheck, cards, fx): the
session state write_script() sets up (reelline/pipeline/script.py), with library defaults."""

from __future__ import annotations

from datetime import date

from reelline.pipeline.step import Context


def publish_date(ctx: Context) -> date | None:
    return ctx.state.get("publish_date")


def script_id(ctx: Context) -> int | None:
    return ctx.state.get("script_id")


def flirt(ctx: Context) -> bool:
    return bool(ctx.state.get("flirt", False))


def story_text(ctx: Context) -> str:
    from reelline.pipeline.step import Artifact

    story = ctx[Artifact.STORY]
    return story if isinstance(story, str) else story["text"]


def pass_json(result) -> dict:
    from reelline.pipeline.script import _pass_json

    return _pass_json(result)
