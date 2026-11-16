"""The punch-up (or straight) pass: prompt, parse, checks, one follow-up turn."""

from __future__ import annotations

import sqlite3
from datetime import date

from reelline.config import conf
from reelline.media import style as reel_style
from reelline.media.prompts import fill
from reelline.media.script_markup import (
    MarkupError,
    Script,
    parse,
)
from reelline.pipeline.dates import georgian_day
from reelline.pipeline.script.base import (
    NO_PREVIOUS_OPENING,
    NO_RECENT_FLIRTS,
    PassResult,
    ScriptError,
    _today,
    _with_failures,
)
from reelline.pipeline.script.cards import repair_cards, uncarded_notes
from reelline.pipeline.script.checks import (
    details_problems,
    fx_problems,
    opening_problems,
    share_problems,
    straight_problems,
)
from reelline.pipeline.script.facts import uncarded
from reelline.pipeline.script.flirt import flirt_block, has_banned_phrase
from reelline.pipeline.script.lock import flirt_line
from reelline.pipeline.script.recent import details_block, shares_block
from reelline.pipeline.script.text import strip_fence
from reelline.providers import CostLedger, LLMError, get_llm


def punchup_pass(
    client,
    brief: str,
    draft: str,
    recent_lines: str,
    *,
    previous: str | None = None,
    publish_date: date | None = None,
    conn: sqlite3.Connection | None = None,
    script_id: int | None = None,
    failures: list[str] | None = None,
    flirt: bool = False,
    recent_flirt_lines: str = NO_RECENT_FLIRTS,
    flirt_angle: str | None = None,
    banned_ok: bool = True,
    recent_details_lines: list[str] | tuple[str, ...] = (),
    recent_share_lines: list[str] | tuple[str, ...] = (),
    log=lambda m: print(m, flush=True),
) -> PassResult:
    """flirt: this reel gets a flirt line (exactly one @flirt paragraph, on `flirt_angle`),
    or none; banned_ok False: the flirt line may not use a banned phrase. Every script
    gets its details line (exactly one @details paragraph), never one of
    `recent_details_lines` (the prompt lists them), and its share line (exactly one @share
    paragraph), never one of `recent_share_lines`. log: the fact cards' repair.

    The style (reelline/media/style.py) picks the prompt: classic prompts/punchup.md (the jokes, fed
    `recent_lines` and `previous`); straight prompts/straight.md (no jokes: `recent_lines`,
    `previous` and the flirt line aren't fed, flirt must be False), recorded as purpose
    punchup_straight, its answer also checked by straight_problems()."""
    style = reel_style.current()
    if flirt and not style.jokes:
        raise ScriptError(f"the {style.name} style has no flirt line (reelline/media/style.py)")
    template = style.punchup_prompt.read_text(encoding="utf-8")
    values = {
        "NEWS_BRIEF": brief,
        "DRAFT": draft,
        "PUBLISH_DATE": georgian_day(publish_date or _today()),
        "RECENT_DETAILS": details_block(recent_details_lines),
        "RECENT_SHARES": shares_block(recent_share_lines),
    }
    if style.jokes:
        values.update(
            {
                "RECENT_LINES": recent_lines,
                "PREVIOUS_OPENING": previous or NO_PREVIOUS_OPENING,
                "FLIRT": flirt_block(flirt, recent_flirt_lines, angle=flirt_angle, banned_ok=banned_ok),
            }
        )
    prompt = _with_failures(fill(template, values, style.punchup_prompt.name), failures, "script")
    messages = [{"role": "user", "content": prompt}]
    version = conf().steps.script.punchup_prompt_version if style.jokes else conf().steps.script.straight_prompt_version
    purpose = "punchup" if style.jokes else "punchup_straight"

    def check(text: str, every_fact: bool = True) -> tuple[Script | None, str | None]:
        try:
            script = parse(text, flirt=flirt, details=True, share=True)
        except MarkupError as exc:
            return None, f"The parser rejected it: {exc}."
        if flirt and not banned_ok and has_banned_phrase(flirt_line(script)):
            return script, conf().steps.script.flirt_banned_note.format(
                recent=conf().steps.script.flirt_banned_every - 1, every=conf().steps.script.flirt_banned_every
            )
        problems = opening_problems(script, previous, draft)
        details = details_problems(script, draft)
        share = share_problems(script, draft)
        fx = fx_problems(script, every_fact=every_fact)
        straight = [] if style.jokes else straight_problems(script, draft)
        said = (
            ([f"The opening breaks the rules: {'; '.join(problems)}."] if problems else [])
            + ([f"The details line breaks the rules: {'; '.join(details)}."] if details else [])
            + ([f"The share line breaks the rules: {'; '.join(share)}."] if share else [])
            + ([f"The fact graphics break the rules: {'; '.join(fx)}."] if fx else [])
            + ([f"The script isn't straight: {'; '.join(straight)}."] if straight else [])
        )
        return script, " ".join(said) or None

    def repair(result: PassResult, problem: str | None, last: bool) -> str | None:
        """The problem left once the fact cards are repaired in code (module docstring): the
        answer with repair_cards() applied replaces it if it passes every check (None left). On
        the last answer a card still missing is never a failure: the repaired answer, else the
        model's, passing every other check is the answer, what has no card noted. Else the answer
        and its problem stand: the follow-up turn, or the failure."""
        if not problem or result.script is None or not style.every_fact_fx or not uncarded(result.script):
            return problem
        text, notes = repair_cards(result.text, result.script)
        if notes:
            script, still = check(text)
            if still is None:
                log(f"  punch-up: fact cards repaired in code ({'; '.join(notes)}); it passes its checks")
                result.text, result.script, result.fx_repaired = text, script, notes
                return None
            log(f"  punch-up: fact cards repaired in code ({'; '.join(notes)}), but it still fails: {still}")
        if not last:
            return problem
        for cand_text, cand_notes in ([(text, notes)] if notes else []) + [(result.text, [])]:
            script, rest = check(cand_text, every_fact=False)
            if rest is None:
                cand_notes = cand_notes + [f"left as it is: {n}" for n in uncarded_notes(script)]
                log(f"  punch-up: a key fact without its card isn't a failure; {'; '.join(cand_notes)}")
                result.text, result.script, result.fx_repaired = cand_text, script, cand_notes
                return None
        return problem

    llm, ledger = get_llm("stylist", client), CostLedger(conn, script_id=script_id)
    try:
        reply = llm.complete(messages, purpose=purpose, prompt_version=version, ledger=ledger)
        result = PassResult(strip_fence(reply.text), [reply])
        result.script, problem = check(result.text)
        problem = repair(result, problem, last=False)
        if problem:
            result.retried = problem
            turn = f"{problem} Output the corrected full script only."
            reply = llm.complete(
                llm.follow_up(messages, reply, turn), purpose=f"{purpose}_retry", prompt_version=version, ledger=ledger
            )
            result.text, result.replies = strip_fence(reply.text), result.replies + [reply]
            result.script, problem = check(result.text)
            problem = repair(result, problem, last=True)
            if problem:
                raise ScriptError(f"punch-up output failed its checks twice: {problem}")
    except LLMError as exc:
        raise ScriptError(str(exc)) from None
    return result


# ---- Draft lock ------------------------------------------------------------------------
