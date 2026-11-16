"""The end card's facts (channel docs/scripts.md "The end card"): the card's Georgian lines (the
fields in remotion/src/outro.json: name, what, who, deadline, cost, apply) from the brief
and the final script, fact-checked like the script.

write_card() asks the endcard role (models.endcard; get_llm(), a JSON schema; the caption's
settings) with prompts/endcard.md for one line per field, or null, and checks the answer
(problems(): reelline/media/outro.py card_problems(), the parser render_reel.py uses too: known
fields, at most each field's maxChars, no markup, "apply" a bare domain; and here:
REQUIRED fields there, the Georgian fields mostly Georgian, no relative day words, "apply"
a domain the brief's "Where to apply" or "How to apply" line has and not a form or a short
link, LINK_HOSTS). A miss gets one follow-up turn naming it; a second fails (CardError).

make_card() is the step: write_card(), then the card's own fact-check
(reelline/pipeline/factcheck.py fact_check(), purpose endcard_factcheck: each line a "sentence" with
the origin "card" and its field's label, LABELS). Failures get one repair round:
write_card() again with them listed, then the fact-check again. A line that still fails
(or failed, when the repair round itself broke) is left off the card, so a wrong fact never
reaches the screen: Card.dropped says which and why, for the review. reelline/pipeline/script.py
write_script() runs it on the final version (each sub-step stored in scripts.passes, so a
retry goes on from where it stopped) and finish() writes the lines to the script's facts
file (reelline/media/outro.py facts_path(), <script>.endcard.json), which render_reel.py draws.
`max_usd` (the CLI's `card --max-usd`): no call starts once what's spent plus CALL_CEILING_USD
would pass it; a repair round that can't start leaves the failing lines off.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from reelline import language
from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.media.outro import Outro, card_problems, load_outro
from reelline.media.prompts import fill
from reelline.media.script_markup import relative_day_words
from reelline.pipeline.dates import georgian_day
from reelline.providers import CostLedger, LLMError, get_llm

# The fields' labels in the fact-check prompt ("S3 (card, deadline): ...").
LABELS = {
    "name": "program name",
    "what": "what it is",
    "who": "who can apply",
    "deadline": "deadline",
    "cost": "cost",
    "apply": "where to apply",
}
# Always on the card (as written; the fact-check can still drop them).
REQUIRED = ("name", "what")
# Mostly Georgian: at least this share of their letters (brand names stay Latin).
GEORGIAN_FIELDS = ("what", "who", "deadline", "cost")
MIN_GEORGIAN = 0.5
# A form or a short link isn't a website to show on the card.
LINK_HOSTS = (
    "forms.gle",
    "docs.google.com",
    "forms.office.com",
    "bit.ly",
    "goo.gl",
    "tinyurl.com",
    "t.co",
    "linktr.ee",
    "ow.ly",
    "cutt.ly",
    "shorturl.at",
    "rb.gy",
    "is.gd",
)
# The brief's lines a domain on the card must come from (reelline/pipeline/brief.py DETAILS labels).
APPLY_LINES = ("Where to apply", "How to apply")
# What one call may cost at most, for max_usd: a Claude card call, a Gemini fact-check.
CALL_CEILING_USD = {"write": 0.03, "check": 0.012}


class CardError(ReellineError, RuntimeError):
    """A card answer that failed its checks twice, a failed call, or the cost cap."""


@dataclass
class Draft:
    """One write_card() answer: the lines (field -> text, nulls left out)."""

    lines: dict[str, str]
    retried: str | None = None
    cost_usd: float = 0.0

    def to_json(self) -> dict:
        return {"lines": self.lines, "retried": self.retried}

    @classmethod
    def from_json(cls, d: dict) -> Draft:
        return cls(d["lines"], d.get("retried"))


@dataclass
class Card:
    """What the card shows, what was left off and why, and the fact-check rounds."""

    lines: dict[str, str]
    dropped: list[dict] = field(default_factory=list)  # [{"field", "text", "reason"}]
    rounds: list[dict] = field(default_factory=list)  # FactCheck.to_json() per round, with its lines
    retried: str | None = None
    repair_error: str | None = None
    cost_usd: float = 0.0

    def to_json(self) -> dict:
        return {
            "lines": self.lines,
            "dropped": self.dropped,
            "rounds": self.rounds,
            "retried": self.retried,
            "repair_error": self.repair_error,
        }

    @classmethod
    def from_json(cls, d: dict) -> Card:
        return cls(d["lines"], d.get("dropped", []), d.get("rounds", []), d.get("retried"), d.get("repair_error"))


def schema(outro: Outro) -> dict:
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {k: nullable for k in outro.keys},
        "required": list(outro.keys),
        "additionalProperties": False,
    }


def _georgian_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    return sum(1 for ch in letters if language.current().is_script_char(ch)) / len(letters) if letters else 1.0


def brief_line(brief: str, label: str) -> str:
    """The text of the brief's "- <label>: ..." line ("" without one)."""
    m = re.search(rf"^- {re.escape(label)}: (.*)$", brief, re.M)
    return m.group(1) if m else ""


def lines_of(data) -> dict[str, str]:
    """The answer's lines: strings with their whitespace collapsed, nulls and empties left out
    (anything else stays as it is, for problems() to name)."""
    if not isinstance(data, dict):
        return data
    out = {}
    for key, value in data.items():
        if value is None:
            continue
        text = " ".join(value.split()) if isinstance(value, str) else value
        if text != "":
            out[key] = text
    return out


def problems(lines: dict, brief: str, outro: Outro) -> list[str]:
    """What's wrong with the card's lines, [] if nothing (module docstring)."""
    found = card_problems(lines, outro)
    if not isinstance(lines, dict):
        return found
    found += [f"{k} is missing; it's always on the card" for k in REQUIRED if not lines.get(k)]
    for key in GEORGIAN_FIELDS:
        text = lines.get(key)
        if isinstance(text, str) and _georgian_share(text) < MIN_GEORGIAN:
            found.append(f"{key} must be Georgian (Latin only for brand names): „{text}“")
    relative = [w for text in lines.values() if isinstance(text, str) for w in relative_day_words(text)]
    if relative:
        found.append(f"relative day words ({', '.join(dict.fromkeys(relative))}); write the date itself")
    domain = lines.get("apply")
    if isinstance(domain, str) and domain:
        if domain in LINK_HOSTS or domain.endswith(tuple(f".{h}" for h in LINK_HOSTS)):
            found.append(f"apply: {domain} is a form or a short link, not a website to show; leave it null")
        elif not any(domain in brief_line(brief, label).lower() for label in APPLY_LINES):
            found.append(
                f'apply: {domain} isn\'t in the brief\'s "Where to apply" or "How to apply" line; '
                "only a domain written there, else null"
            )
    return found


def _parse(text: str) -> dict | str:
    """The answer's JSON object, or a problem."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"the answer isn't JSON ({exc})"
    return data if isinstance(data, dict) else "the answer isn't a JSON object"


def _with_failures(prompt: str, failures: list[str] | None) -> str:
    if not failures:
        return prompt
    lines = "\n".join(f"- {f}" for f in failures)
    return (
        f"{prompt}\n\n# Fix these problems\nA fact-check of the previous card found lines the brief doesn't "
        f"support. Fix or leave out (null) each of them; stay strictly within the brief's facts:\n{lines}\n"
    )


def write_card(
    client,
    brief: str,
    script_text: str,
    *,
    publish_date: date,
    outro: Outro | None = None,
    conn: sqlite3.Connection | None = None,
    script_id: int | None = None,
    failures: list[str] | None = None,
    budget: Callable[[str], None] = lambda kind: None,
) -> Draft:
    """The card's lines from the endcard role's model, checked (problems()); one follow-up turn on a
    problem, CardError on a second. `budget(kind)` runs before each call (raises to stop it)."""
    outro = outro or load_outro()
    values = {"NEWS_BRIEF": brief, "SCRIPT": script_text, "PUBLISH_DATE": georgian_day(publish_date)}
    prompt = _with_failures(
        fill(conf().steps.endcard.prompt.read_text(encoding="utf-8"), values, conf().steps.endcard.prompt.name),
        failures,
    )
    messages = [{"role": "user", "content": prompt}]
    version = conf().steps.endcard.prompt_version
    shape = schema(outro)
    llm, ledger = get_llm("endcard", client), CostLedger(conn, script_id=script_id)

    def check(text: str) -> tuple[dict, list[str]]:
        data = _parse(text)
        if isinstance(data, str):
            return {}, [data]
        lines = lines_of(data)
        return lines, problems(lines, brief, outro)

    try:
        budget("write")
        reply = llm.complete(messages, purpose="endcard", prompt_version=version, ledger=ledger, schema=shape)
        spent = reply.cost_usd or 0.0
        lines, found = check(reply.text)
        retried = None
        if found:
            retried = "; ".join(found)
            turn = "The card has problems: " + "; ".join(found) + ". Output the corrected JSON only."
            budget("write")
            reply = llm.complete(
                llm.follow_up(messages, reply, turn),
                purpose="endcard_retry",
                prompt_version=version,
                ledger=ledger,
                schema=shape,
            )
            spent += reply.cost_usd or 0.0
            lines, found = check(reply.text)
            if found:
                raise CardError("the end card failed its checks twice: " + "; ".join(found))
    except LLMError as exc:
        raise CardError(str(exc)) from None
    return Draft({k: lines[k] for k in outro.keys if k in lines}, retried, spent)


def card_report(lines: dict[str, str], outro: Outro) -> dict:
    """The fact-check's input (a draft-lock report's "final"): one sentence per line, in the
    card's order, origin "card", with its field and label."""
    return {
        "final": [
            {"text": lines[k], "origin": "card", "field": k, "label": LABELS[k]} for k in outro.keys if k in lines
        ]
    }


def check_card(
    conn: sqlite3.Connection | None,
    client,
    source_text: str,
    brief: str,
    lines: dict[str, str],
    *,
    outro: Outro | None = None,
    script_id: int | None = None,
):
    """The card's fact-check (reelline/pipeline/factcheck.py), each claim with its line's field."""
    from reelline.pipeline.factcheck import fact_check

    report = card_report(lines, outro or load_outro())
    fc = fact_check(conn, client, source_text, brief, report, script_id, purpose="endcard_factcheck")
    for c in fc.claims:
        c["field"] = report["final"][c["sentence"] - 1]["field"]
    return fc


def _fc_json(fc, lines: dict[str, str]) -> dict:
    return {**fc.to_json(), "lines": lines}


def _fc_from(d: dict):
    from reelline.pipeline.factcheck import FactCheck

    return FactCheck(d["claims"], d.get("inserted", []))


def make_card(
    conn: sqlite3.Connection | None,
    claude,
    gemini,
    *,
    brief: str,
    script_text: str,
    source_text: str,
    publish_date: date,
    script_id: int | None = None,
    saved=None,
    max_usd: float | None = None,
    log: Callable[[str], None] = print,
) -> Card:
    """The card's lines (module docstring). `saved(key, run, to_json, from_json)` keeps each
    sub-step's result (reelline/pipeline/script.py write_script(): scripts.passes); default: run it."""
    from reelline.pipeline.factcheck import FactCheckError, failure_text

    outro = load_outro()
    saved = saved or (lambda key, run, to_json, from_json: run())
    spent = [0.0]

    def budget(kind: str) -> None:
        if max_usd is not None and spent[0] + CALL_CEILING_USD[kind] > max_usd:
            raise CardError(f"cost cap: ${spent[0]:.4f} spent, a {kind} call could pass ${max_usd:.2f}")

    def write(failures=None) -> Draft:
        d = write_card(
            claude,
            brief,
            script_text,
            publish_date=publish_date,
            outro=outro,
            conn=conn,
            script_id=script_id,
            failures=failures,
            budget=budget,
        )
        spent[0] += d.cost_usd
        return d

    def check(lines: dict[str, str]):
        budget("check")
        fc = check_card(conn, gemini, source_text, brief, lines, outro=outro, script_id=script_id)
        spent[0] += fc.cost_usd
        return fc

    first = saved("card/write", write, Draft.to_json, Draft.from_json)
    log(f"  end card: {', '.join(first.lines)}" + (f"; retried: {first.retried}" if first.retried else ""))
    fc = saved("card/factcheck", lambda: check(first.lines), lambda f: _fc_json(f, first.lines), _fc_from)
    log(f"  end card fact-check: {fc.to_json()['counts']}")
    final, rounds, repair_error = first, [_fc_json(fc, first.lines)], None
    if not fc.passed:
        failures = [failure_text(c) for c in fc.failures]
        log(f"  end card: {len(failures)} failure(s); writing it again")
        try:
            second = saved("card/rewrite", lambda: write(failures), Draft.to_json, Draft.from_json)
            fc = saved("card/refactcheck", lambda: check(second.lines), lambda f: _fc_json(f, second.lines), _fc_from)
            final = second
            rounds.append(_fc_json(fc, second.lines))
            log(f"  end card fact-check (repair): {fc.to_json()['counts']}")
        except (CardError, FactCheckError) as exc:
            repair_error = str(exc)
            log(f"  end card repair round failed ({exc}); leaving the failing lines off")
    failing = {c["field"]: c for c in fc.failures}
    dropped = [
        {
            "field": k,
            "text": final.lines[k],
            "reason": f"{c['verdict']}: {c['claim']}" + (f" ({c['note']})" if c.get("note") else ""),
        }
        for k, c in failing.items()
    ]
    lines = {k: v for k, v in final.lines.items() if k not in failing}
    for d in dropped:
        log(f"  end card: left off {d['field']} „{d['text']}“ ({d['reason']})")
    return Card(lines, dropped, rounds, first.retried, repair_error, spent[0])


def summary_lines(card: dict | None) -> list[str]:
    """The review's end-card lines from a stored Card (scripts.passes["card"]); [] without one."""
    if not card:
        return ["🪧 End card: none (a script made before the card): footer only"]
    shown = ", ".join(card["lines"]) or "no facts"
    if not card.get("dropped"):
        return [f"🪧 End card: ✅ {len(card['lines'])} line(s) ({shown})"]
    out = [f"🪧 End card: ⚠️ {len(card['lines'])} line(s) ({shown}); left off {len(card['dropped'])}:"]
    out += [f"  - {d['field']} „{d['text']}“: {d['reason']}" for d in card["dropped"]]
    return out
