"""The story pass: hook examples, the filled story prompt, story_pass()."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from reelline.config import conf
from reelline.media import style as reel_style
from reelline.media.prompts import fill
from reelline.paths import REPO_ROOT
from reelline.pipeline import script as _s
from reelline.pipeline.dates import georgian_day
from reelline.pipeline.script.base import (
    _EXAMPLES_LINE,
    OPENINGS_MARKER,
    PassResult,
    ScriptError,
    _today,
    _with_failures,
)
from reelline.pipeline.script.openings import split_openings
from reelline.providers import CostLedger, LLMError, get_llm


def _example(entry) -> str | None:
    """One hook example's line, or None for an entry without an opening."""
    if not isinstance(entry, dict) or not isinstance(entry.get("opening"), str):
        return None
    opening = " ".join(entry["opening"].split()).strip("„“\"' ")
    if not opening:
        return None

    def number(key):
        value = entry.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None

    half, viewed = number("half_gone_s"), number("avg_pct_viewed")
    how = ([f"half the viewers gone at {half:.1f} s"] if half is not None else []) + (
        [f"{viewed:.0f}% of the video watched on average"] if viewed is not None else []
    )
    return f"- „{opening}“" + (f" ({', '.join(how)})" if how else "")


def hook_examples(path: Path | None = None) -> tuple[str, str]:
    """{{HOOK_EXAMPLES}} from HOOK_EXAMPLES (data/hook_examples.json, reelline/pipeline/retention.py writes it):
    the story prompt's section of openings that held viewers and openings that lost them, at
    most steps.script.hook_examples_max of each, in the file's order; "" (the section left out)
    when the file is missing, unreadable or has none, or in a style that doesn't feed them
    (reelline/media/style.py hook_examples: straight). And a line for the log saying which."""
    path = path or conf().storage.hook_examples
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    style = reel_style.current()
    if not style.hook_examples:
        return "", f"hook examples: none (not fed in the {style.name} style)"
    if not path.is_file():
        return "", f"hook examples: none (no {shown})"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as exc:
        return "", f"hook examples: none ({shown} unreadable: {exc})"
    most = conf().steps.script.hook_examples_max
    lists = {}
    for key in ("winners", "flops"):
        entries = data.get(key) if isinstance(data.get(key), list) else []
        lists[key] = [line for line in map(_example, entries) if line][:most]
    if not lists["winners"] and not lists["flops"]:
        return "", f"hook examples: none ({shown} has none)"
    parts = [
        f"# Openings from our videos\nFirst sentences of earlier {conf().channel.shown_name()} videos and how they held viewers. Learn the "
        "pattern from them, never copy one: not its words, not its facts. Every opening still follows the rules "
        "above."
    ]
    if lists["winners"]:
        parts.append("Openings that held viewers:\n" + "\n".join(lists["winners"]))
    if lists["flops"]:
        parts.append("Openings that lost them:\n" + "\n".join(lists["flops"]))
    updated = f", updated {data['updated']}" if isinstance(data.get("updated"), str) else ""
    return "\n\n".join(parts), (
        f"hook examples: {len(lists['winners'])} winner(s), {len(lists['flops'])} flop(s) ({shown}{updated})"
    )


def fill_story(template: str, brief: str, publish_day: str, examples: str = "", name: str | None = None) -> str:
    """The story prompt filled: the brief, the publish day (georgian_day()) and the hook
    examples (hook_examples()); without examples the {{HOOK_EXAMPLES}} line is taken out, so
    the section is omitted cleanly. In a style without the opening emotion (reelline/media/style.py
    straight) the rubric's delivery line asks for a straight opening (STRAIGHT_OPENING)."""
    name = name or conf().steps.script.story_prompt.name
    if not reel_style.current().opening_emotion and conf().steps.script.straight_opening:
        old, new = conf().steps.script.straight_opening
        if template.count(old) != 1:
            raise ScriptError(f"{name}: the opening rubric's {old!r} isn't there once (STRAIGHT_OPENING)")
        template = template.replace(old, new)
    values = {"NEWS_BRIEF": brief, "PUBLISH_DATE": publish_day}
    if examples.strip():
        values["HOOK_EXAMPLES"] = examples.strip()
    else:
        template = _EXAMPLES_LINE.sub("", template)
    return fill(template, values, name)


def story_pass(
    client,
    brief: str,
    *,
    publish_date: date | None = None,
    conn: sqlite3.Connection | None = None,
    script_id: int | None = None,
    failures: list[str] | None = None,
    examples: str | None = None,
) -> PassResult:
    """publish_date: the day the video goes out (the slot's; today when None). examples:
    {{HOOK_EXAMPLES}} (hook_examples()'s when None; "" leaves the section out). The answer's
    openings block is taken off the story (split_openings()) into PassResult.openings."""
    if examples is None:
        examples = hook_examples()[0]
    prompt = _with_failures(
        fill_story(
            conf().steps.script.story_prompt.read_text(encoding="utf-8"),
            brief,
            georgian_day(publish_date or _today()),
            examples,
        ),
        failures,
        "story",
    )
    messages = [{"role": "user", "content": prompt}]
    version = conf().steps.script.story_prompt_version
    llm, ledger = get_llm("writer", client), CostLedger(conn, script_id=script_id)
    try:
        reply = llm.complete(messages, purpose="story", prompt_version=version, ledger=ledger)
        story, openings = split_openings(reply.text)
        result = PassResult(story, [reply], openings=openings)
        problems = _s.story_problems(story)
        if problems:
            result.retried = "; ".join(problems)
            turn = (
                "The story has problems: " + "; ".join(problems) + ". Output the corrected story, plain "
                f"Georgian text, then the {OPENINGS_MARKER} block as before."
            )
            reply = llm.complete(
                llm.follow_up(messages, reply, turn), purpose="story_retry", prompt_version=version, ledger=ledger
            )
            story, openings = split_openings(reply.text)
            result.text, result.openings, result.replies = story, openings, result.replies + [reply]
            problems = _s.story_problems(story)
            if problems:
                raise ScriptError("story pass failed twice: " + "; ".join(problems))
    except LLMError as exc:
        raise ScriptError(str(exc)) from None
    return result


# ---- Punch-up pass ---------------------------------------------------------------------
