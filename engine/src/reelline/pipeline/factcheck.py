"""Fact-check a final script against its story's source text (Gemini,
prompts/factcheck.md, JSON schema).

fact_check() numbers the final script's display sentences (the draft-lock report's
"final" list, tag paragraph left out) and asks for every factual claim about the
news with a verdict (supported | unsupported | distorted | general_definition), the
evidence quote from the source and a note. Jokes about the viewer, groups or the host aren't
claims; a joke asserting something about the news subject is. A general_definition (since
prompt 3, 2026-10-23) is one short clause saying what a role, a field or a term means in
general, with nothing about this program (prompts/story.md "Say what it is, in plain
words"): not a failure. Only one sentence may have one: a general_definition in a later
sentence is made unsupported here (SECOND_DEFINITION), whatever the model said. Each
claim gets its sentence's text and origin from the draft-lock diff ("draft", "changed"
or "punchup"), so the caller can route a failure to the pass that made it. Evidence
quotes not found in the source are marked (evidence_found: false) but don't change the
verdict.

The end card's lines (reelline/pipeline/endcard.py) are checked the same way, in a call of their own:
"final" entries with the origin "card" and their field's label, numbered "S<n> (card,
<label>)" in the prompt (prompt 4). A card line is never a general definition: one gets
unsupported here (CARD_DEFINITION).

The fact graphics (reelline/pipeline/fx.py) are checked the same way, in a call of their own: "final"
entries with the origin "fx", their template's label and their paragraph as context,
numbered "S<n> (fx, <label>, on: „<paragraph>“)" (prompt 5). Never a general definition
either (FX_DEFINITION).

The sentences punch-up inserted are marked "(inserted)" in the prompt, and each must
get an entry in "inserted": whether it asserts anything about the news subject
(news_assertion) and why. Every inserted sentence reviewed exactly once, only those,
and a news_assertion with at least one claim from it, or the result is off-schema: an
inserted sentence can't pass by being skipped.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from reelline.config import conf
from reelline.core.errors import CheckFailed
from reelline.media.prompts import fill
from reelline.providers import CostLedger, LLMError, get_llm

DEFINITION = "general_definition"
VERDICTS = ("supported", "unsupported", "distorted", DEFINITION)
INSERTED_MARK = " (inserted)"  # after "S<n>" for a sentence punch-up inserted
FAILING = ("unsupported", "distorted")
SECOND_DEFINITION = "a second general definition: the script may define only one role, field or term"
CARD_DEFINITION = "a general definition on the end card: the card states only this program's facts"
FX_DEFINITION = "a general definition on a fact graphic: it shows only this program's facts"

_OPT = {"anyOf": [{"type": "string"}, {"type": "null"}]}
CLAIM = {
    "type": "object",
    "properties": {
        "sentence": {"type": "integer", "minimum": 1},
        "claim": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "evidence": _OPT,
        "note": _OPT,
    },
    "required": ["sentence", "claim", "verdict", "evidence", "note"],
}
INSERTED = {
    "type": "object",
    "properties": {
        "sentence": {"type": "integer", "minimum": 1},
        "news_assertion": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["sentence", "news_assertion", "reason"],
}
SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": CLAIM}, "inserted": {"type": "array", "items": INSERTED}},
    "required": ["claims", "inserted"],
}


class FactCheckError(CheckFailed):
    """A fact-check call that failed or came back off-schema."""


@dataclass
class FactCheck:
    claims: list[dict] = field(default_factory=list)
    inserted: list[dict] = field(default_factory=list)  # the review of each punch-up sentence
    cost_usd: float = 0.0  # this call's (not stored: llm_calls has it)

    @property
    def failures(self) -> list[dict]:
        return [c for c in self.claims if c["verdict"] in FAILING]

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_json(self) -> dict:
        return {
            "passed": self.passed,
            "claims": self.claims,
            "inserted": self.inserted,
            "counts": {v: sum(1 for c in self.claims if c["verdict"] == v) for v in VERDICTS},
        }


def failure_text(claim: dict) -> str:
    """A failure as a line for a re-run prompt and for the review."""
    note = f" ({claim['note']})" if claim.get("note") else ""
    return f'"{claim["sentence_text"]}": {claim["verdict"]}, {claim["claim"]}{note}'


def _squash(text: str) -> str:
    return " ".join((text or "").casefold().split())


def fact_check(
    conn: sqlite3.Connection | None,
    client,
    source_text: str,
    brief: str,
    report: dict,
    script_id: int | None = None,
    purpose: str = "factcheck",
) -> FactCheck:
    final = report["final"]
    numbered = "\n".join(f"S{i}{_mark(s)}: {s['text']}" for i, s in enumerate(final, 1))
    source = source_text
    if len(source) > conf().steps.factcheck.max_source_chars:
        source = source[: conf().steps.factcheck.max_source_chars] + " [...]"
    template = conf().steps.factcheck.prompt.read_text(encoding="utf-8")
    prompt = fill(
        template, {"SOURCE": source, "BRIEF": brief, "SENTENCES": numbered}, conf().steps.factcheck.prompt.name
    )
    try:
        result = get_llm("checker", client).complete_json(
            prompt,
            SCHEMA,
            purpose=purpose,
            prompt_version=conf().steps.factcheck.prompt_version,
            ledger=CostLedger(conn, script_id=script_id),
        )
    except LLMError as exc:
        raise FactCheckError(str(exc)) from None
    claims = result.data.get("claims")
    if not isinstance(claims, list):
        raise FactCheckError("fact-check has no claims list")
    out = []
    haystack = _squash(source_text)
    for c in claims:
        if not isinstance(c, dict) or c.get("verdict") not in VERDICTS:
            raise FactCheckError(f"fact-check claim off-schema: {c!r}")
        n = c.get("sentence")
        if not isinstance(n, int) or not 1 <= n <= len(final):
            raise FactCheckError(f"fact-check claim points at sentence {n!r}; the script has {len(final)}")
        evidence = c.get("evidence") or None
        out.append(
            {
                "sentence": n,
                "sentence_text": final[n - 1]["text"],
                "origin": final[n - 1]["origin"],
                "claim": " ".join(str(c.get("claim") or "").split()),
                "verdict": c["verdict"],
                "evidence": evidence,
                "evidence_found": bool(evidence) and _squash(evidence) in haystack,
                "note": c.get("note") or None,
            }
        )
    return FactCheck(_one_definition(out), _inserted_review(result.data.get("inserted"), final, out), result.cost_usd)


def _mark(sentence: dict) -> str:
    """After "S<n>": " (inserted)" for a sentence punch-up inserted, " (card, <label>)" for an
    end-card line, " (fx, <label>, on: „<paragraph>“)" for a fact graphic, else nothing."""
    if sentence["origin"] == "punchup":
        return INSERTED_MARK
    if sentence["origin"] == "fx":
        return f" (fx, {sentence['label']}, on: „{sentence['context']}“)"
    return f" (card, {sentence['label']})" if sentence["origin"] == "card" else ""


def _one_definition(claims: list[dict]) -> list[dict]:
    """General definitions in one sentence at most (the first one's): any in a later
    sentence become unsupported (SECOND_DEFINITION); none on an end-card line (CARD_DEFINITION)
    or a fact graphic (FX_DEFINITION)."""
    for c in claims:
        if c["verdict"] == DEFINITION and c["origin"] in ("card", "fx"):
            c.update(verdict="unsupported", note=CARD_DEFINITION if c["origin"] == "card" else FX_DEFINITION)
    first = min((c["sentence"] for c in claims if c["verdict"] == DEFINITION), default=None)
    for c in claims:
        if c["verdict"] == DEFINITION and c["sentence"] != first:
            c.update(verdict="unsupported", note=SECOND_DEFINITION)
    return claims


def _inserted_review(review, final: list[dict], claims: list[dict]) -> list[dict]:
    """The model's review of the inserted sentences, checked for coverage."""
    if not isinstance(review, list):
        raise FactCheckError("fact-check has no inserted list")
    expected = {i for i, s in enumerate(final, 1) if s["origin"] == "punchup"}
    with_claims = {c["sentence"] for c in claims}
    out, seen = [], set()
    for r in review:
        n = r.get("sentence") if isinstance(r, dict) else None
        if n not in expected or n in seen or not isinstance(r.get("news_assertion"), bool):
            raise FactCheckError(f"fact-check inserted entry off-schema or not an inserted sentence: {r!r}")
        if r["news_assertion"] and n not in with_claims:
            raise FactCheckError(
                f"fact-check says inserted sentence S{n} asserts something about the news but lists no claim from it"
            )
        seen.add(n)
        out.append(
            {
                "sentence": n,
                "sentence_text": final[n - 1]["text"],
                "news_assertion": r["news_assertion"],
                "reason": " ".join(str(r.get("reason") or "").split()),
            }
        )
    if expected - seen:
        missing = ", ".join(f"S{n}" for n in sorted(expected - seen))
        raise FactCheckError(f"fact-check skipped inserted sentence(s) {missing}")
    return sorted(out, key=lambda r: r["sentence"])
