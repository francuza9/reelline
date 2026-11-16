"""The story pass's OPENINGS block: split off the story, checked, and noted for the review."""

from __future__ import annotations

import json

from reelline.pipeline.script.base import _OPENINGS_LINE, ALT_OPENINGS, OPENING_RUBRIC, OPENINGS_MARKER
from reelline.pipeline.script.lock import sentences
from reelline.pipeline.script.text import _same, strip_fence


def _score(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        return None
    return int(value) if 0 <= value <= OPENING_RUBRIC else None


def _openings(data, story: str) -> dict:
    """The openings block's JSON, checked: {"used": {"opening": the story's first sentence,
    "score"}, "runners_up": [{"opening", "score"}, ...]} (at most ALT_OPENINGS, the used one
    left out, a score outside 0-OPENING_RUBRIC None), or {"problem": why}."""
    if not isinstance(data, dict) or not isinstance(data.get("runners_up"), list):
        return {"problem": "the OPENINGS block has no runners_up list"}
    first = (sentences(story) or [""])[0]
    runners = []
    for item in data["runners_up"]:
        opening = item.get("opening") if isinstance(item, dict) else item
        if not isinstance(opening, str) or not opening.strip() or (first and _same(opening, first)):
            continue
        runners.append(
            {
                "opening": " ".join(opening.split()),
                "score": _score(item.get("score")) if isinstance(item, dict) else None,
            }
        )
    if not runners:
        return {"problem": "the OPENINGS block lists no other opening"}
    return {"used": {"opening": first, "score": _score(data.get("used_score"))}, "runners_up": runners[:ALT_OPENINGS]}


def split_openings(text: str) -> tuple[str, dict]:
    """The story pass's answer -> (the story, its openings): the block after the last
    OPENINGS: line (prompts/story.md "Length and output"; a fenced block, or a JSON object
    with runners_up ending the answer without the line, taken too), read by _openings(), or
    {"problem": why} when it's missing or unreadable. The story never keeps the block."""
    lines = text.strip().splitlines()
    at = next((i for i in range(len(lines) - 1, -1, -1) if _OPENINGS_LINE.match(lines[i])), None)
    if at is not None:
        story = "\n".join(lines[:at]).strip()
        raw = strip_fence("\n".join([_OPENINGS_LINE.sub("", lines[at], count=1)] + lines[at + 1 :]))
        try:
            return story, _openings(json.loads(raw), story)
        except ValueError as exc:
            return story, {"problem": f"the OPENINGS block isn't JSON ({exc})"}
    for i in range(len(lines) - 1, max(len(lines) - 12, 0) - 1, -1):
        if not lines[i].lstrip().startswith(("{", "```")):
            continue
        try:
            data = json.loads(strip_fence("\n".join(lines[i:])))
        except ValueError:
            continue
        if isinstance(data, dict) and "runners_up" in data:
            story = "\n".join(lines[:i]).strip()
            return story, _openings(data, story)
    return text.strip(), {"problem": f"no {OPENINGS_MARKER} block"}


def _scored(opening: dict) -> str:
    return f"{opening['score']}/{OPENING_RUBRIC}" if opening.get("score") is not None else "?"


def openings_note(openings: dict | None) -> str:
    """The openings in a few words, for the log."""
    if not openings:
        return "no openings block (a story before story 11)"
    if "problem" in openings:
        return f"no other openings ({openings['problem']})"
    return f"opening scored {_scored(openings['used'])}, runners-up " + ", ".join(map(_scored, openings["runners_up"]))
