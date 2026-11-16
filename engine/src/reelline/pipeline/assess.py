"""Assess stories with Gemini (prompts/assess.md): one result per story, cached in
the assessments table (reelline/pipeline/db.py), never redone unless forced.

assess_stories() sends the stories in batches of steps.pick.assess_batch, each story's
text cut to steps.pick.assess_max_chars, and validates every result strictly (every enum
in its set, the score in 0-10); an off-schema result fails the batch loudly. A story
the model leaves out is asked again on its own once; still missing, it stays
unassessed (picked up next run) and the batch goes on. Ids outside the batch are
ignored, a repeated id keeps its first result; only a batch with no result at all
fails. Two clean-ups, both recorded in the raw JSON:
  - a deadline (event date) is kept only if its deadline_quote (event_date_quote)
    appears in the story's text (the model must copy the words that state it; a date
    it can't quote is dropped)
  - fields for another topic are nulled (no edu_type on a tech story)
  - an education story the model gave no opportunity value counts as not an
    opportunity (_opportunity_null)
hook_strength (0-10, every story) and tech_significance (0-10, tech stories) are
integers, checked strictly; pick folds them into the rank (reelline/pipeline/scoring.py).
The base score isn't asked for: it's computed from the education fields
(reelline/pipeline/scoring.py education_base(); 0 for any other topic).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.media.prompts import fill
from reelline.pipeline import db
from reelline.providers import CostLedger, LLMError, LLMUsage, get_llm

TOPICS = ("education", "tech", "startups", "other")
EDU_TYPES = (
    "scholarship",
    "grant",
    "course",
    "training",
    "bootcamp",
    "program",
    "admissions",
    "olympiad",
    "competition",
    "hackathon",
    "exam",
    "other",
)
TIERS = (
    "university",
    "government",
    "international",
    "major_company",
    "established_academy",
    "established_ngo",
    "other",
)
COSTS = ("free", "funded", "paid", "unknown")
RELEVANCE = ("in_georgia", "open_to_georgia", "unclear", "not_open")
OPEN_STATUSES = ("open", "upcoming", "closed", "unclear")
REACHES = ("broad", "medium", "narrow")
NOVELTIES = ("new", "ongoing")

EDU_FIELDS = (
    "edu_type",
    "opportunity",
    "open_status",
    "provider",
    "provider_tier",
    "cost",
    "marketing_or_low_credibility",
    "georgia_relevance",
    "reach",
    "novelty",
    "deadline",
    "deadline_quote",
    "event_date",
    "event_date_quote",
    "who_can_apply",
)
# Dates the model must back with a quote from the text: (field, its quote field).
QUOTED_DATES = (("deadline", "deadline_quote"), ("event_date", "event_date_quote"))
TECH_FIELDS = ("big_news", "niche", "tech_significance")


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


_STR = {"type": "string"}
_BOOL = {"type": "boolean"}

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "banned": _BOOL,
        "banned_reason": _nullable(_STR),
        "policy_angle": _BOOL,
        "topic": {"type": "string", "enum": list(TOPICS)},
        "edu_type": _nullable({"type": "string", "enum": list(EDU_TYPES)}),
        "opportunity": _nullable(_BOOL),
        "open_status": _nullable({"type": "string", "enum": list(OPEN_STATUSES)}),
        "provider": _nullable(_STR),
        "provider_tier": _nullable({"type": "string", "enum": list(TIERS)}),
        "cost": _nullable({"type": "string", "enum": list(COSTS)}),
        "marketing_or_low_credibility": _nullable(_BOOL),
        "georgia_relevance": _nullable({"type": "string", "enum": list(RELEVANCE)}),
        "reach": _nullable({"type": "string", "enum": list(REACHES)}),
        "novelty": _nullable({"type": "string", "enum": list(NOVELTIES)}),
        "deadline": _nullable({"type": "string", "description": "YYYY-MM-DD, only if stated in the text"}),
        "deadline_quote": _nullable(_STR),
        "event_date": _nullable({"type": "string", "description": "YYYY-MM-DD, only if stated in the text"}),
        "event_date_quote": _nullable(_STR),
        "who_can_apply": _nullable(_STR),
        "big_news": _nullable(_BOOL),
        "niche": _nullable(_BOOL),
        "tech_significance": _nullable({"type": "integer", "description": "0-10, tech stories only"}),
        "hook_strength": {"type": "integer", "description": "0-10"},
        "same_program_key": _STR,
        "summary": _STR,
    },
}
ITEM_SCHEMA["required"] = list(ITEM_SCHEMA["properties"])
SCHEMA = {
    "type": "object",
    "properties": {"stories": {"type": "array", "items": ITEM_SCHEMA}},
    "required": ["stories"],
}


class AssessError(ReellineError, RuntimeError):
    """An assessment batch that failed or came back off-schema."""


@dataclass(frozen=True)
class StoryInput:
    id: int
    source: str
    topic_hint: str
    published: str
    url: str
    title: str
    text: str


def story_input(row: sqlite3.Row, source_name: str) -> StoryInput:
    return StoryInput(
        id=row["id"],
        source=source_name,
        topic_hint=row["topic_hint"],
        published=(row["published_at"] or "")[:10],
        url=row["canonical_url"],
        title=row["title"],
        text=row["text"],
    )


def render_stories(stories: list[StoryInput]) -> str:
    blocks = []
    for s in stories:
        text = (
            s.text
            if len(s.text) <= conf().steps.pick.assess_max_chars
            else s.text[: conf().steps.pick.assess_max_chars] + " [...]"
        )
        blocks.append(
            f'<story id="{s.id}">\nsource: {s.source} (topic hint: {s.topic_hint})\npublished: {s.published}\n'
            f"url: {s.url}\ntitle: {s.title}\ntext:\n{text}\n</story>"
        )
    return "\n\n".join(blocks)


def build_prompt(stories: list[StoryInput], today: date, publish_date: date | None = None) -> str:
    """The prompt for one batch: today, the slot's publish date (the day the video goes
    out; today when not for a slot) and the stories."""
    template = conf().steps.pick.assess_prompt.read_text(encoding="utf-8")
    return fill(
        template,
        {
            "TODAY": today.isoformat(),
            "PUBLISH_DATE": (publish_date or today).isoformat(),
            "STORIES": render_stories(stories),
        },
        conf().steps.pick.assess_prompt.name,
    )


def _squash(text: str) -> str:
    return " ".join(text.casefold().split())


def _check_enum(result: dict, key: str, allowed: tuple, nullable: bool = True) -> None:
    value = result.get(key)
    if value is None and nullable:
        return
    if value not in allowed:
        raise AssessError(f"story {result.get('id')}: {key}={value!r} not in {allowed}")


def validate(result: dict, story: StoryInput) -> tuple[dict, dict]:
    """(fields for the assessments table, the raw result with any clean-up notes)."""
    raw = dict(result)
    missing = [k for k in ITEM_SCHEMA["required"] if k not in result]
    if missing:
        raise AssessError(f"story {story.id}: missing {', '.join(missing)}")
    for key in ("banned", "policy_angle"):
        if not isinstance(result[key], bool):
            raise AssessError(f"story {story.id}: {key} must be a bool")
    _check_enum(result, "topic", TOPICS, nullable=False)
    for key, allowed in (
        ("edu_type", EDU_TYPES),
        ("provider_tier", TIERS),
        ("cost", COSTS),
        ("georgia_relevance", RELEVANCE),
        ("open_status", OPEN_STATUSES),
        ("reach", REACHES),
        ("novelty", NOVELTIES),
    ):
        _check_enum(result, key, allowed)
    if (
        result["topic"] == conf().steps.pick.topic
        and result.get("opportunity") is not None
        and not isinstance(result["opportunity"], bool)
    ):
        raise AssessError(f"story {story.id}: opportunity must be a bool for a {conf().steps.pick.topic} story")
    for key in ("hook_strength", "tech_significance"):
        value = result.get(key)
        if value is None and (key == "tech_significance" and result["topic"] != conf().steps.pick.weekly_topic):
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
            raise AssessError(f"story {story.id}: {key}={value!r} must be an integer 0-10")
    key = " ".join(str(result["same_program_key"] or "").lower().split())
    summary = " ".join(str(result["summary"] or "").split())
    if not key or not summary:
        raise AssessError(f"story {story.id}: empty same_program_key or summary")

    fields = {k: result.get(k) for k in db.ASSESSMENT_FIELDS}
    fields.update(same_program_key=key, summary=summary)
    if not result["banned"]:
        fields["banned_reason"] = None
    if result["topic"] != conf().steps.pick.topic:
        for k in EDU_FIELDS:
            fields.pop(k, None)
    if result["topic"] != conf().steps.pick.weekly_topic:
        for k in TECH_FIELDS:
            fields.pop(k, None)

    haystack = _squash(story.title + " " + story.text)
    for name, quote_name in QUOTED_DATES:
        value = fields.get(name)
        if value is None:
            continue
        quote = result.get(quote_name) or ""
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError):
            raw[f"_{name}_dropped"] = f"not a YYYY-MM-DD date: {value!r}"
            fields[name] = None
            continue
        if not quote.strip() or _squash(quote) not in haystack:
            raw[f"_{name}_dropped"] = f"{quote_name} not found in the text: {quote!r}"
            fields[name] = None
    if result["topic"] == conf().steps.pick.topic and fields.get("opportunity") is None:
        # The model left it open: not an opportunity (the safe side), recorded.
        raw["_opportunity_null"] = "the model gave no opportunity value: counted as not an opportunity"
        fields["opportunity"] = False
    fields["base_score"] = base_score(fields)
    for k in ("marketing_or_low_credibility", "big_news", "niche", "opportunity"):
        if fields.get(k) is not None:
            fields[k] = int(bool(fields[k]))
    fields["banned"] = int(result["banned"])
    fields["policy_angle"] = int(result["policy_angle"])
    return fields, raw


def base_score(fields: dict) -> float:
    """The stored base score: education_base() for an education story, else 0."""
    from reelline.pipeline.scoring import Candidate, education_base

    if fields.get("topic") != conf().steps.pick.topic:
        return 0.0
    probe = Candidate(
        story_id=0,
        title="",
        source_id="",
        canonical_url="",
        status="new",
        published_at=None,
        date_source=None,
        fetched_at=datetime.now(UTC),
        topic=conf().steps.pick.topic,
        banned=False,
        base_score=0.0,
        same_program_key="",
        summary="",
        provider_tier=fields.get("provider_tier"),
        open_status=fields.get("open_status"),
        reach=fields.get("reach"),
        novelty=fields.get("novelty"),
    )
    return education_base(probe)[0]


def _assess(
    conn: sqlite3.Connection | None,
    client,
    stories: list[StoryInput],
    today: date,
    publish_date: date | None,
    purpose: str = "assess",
    log=lambda message: None,
):
    """One assessor call for the batch, every returned result validated: (the LLMReply,
    [(story, fields, raw)], [stories the model left out]). An id that isn't in the batch
    is ignored and logged; a repeated id keeps its first result. A story the model
    leaves out is returned for another try, not a failure of the whole batch."""
    try:
        result = get_llm("assessor", client).complete_json(
            build_prompt(stories, today, publish_date),
            SCHEMA,
            purpose=purpose,
            prompt_version=conf().steps.pick.assess_prompt_version,
            ledger=CostLedger(conn),
            items=len(stories),
        )
    except LLMError as exc:
        raise AssessError(str(exc)) from None
    items = result.data.get("stories")
    if not isinstance(items, list):
        raise AssessError("assessment has no stories list")
    by_id = {s.id: s for s in stories}
    validated, seen, extra, repeated = [], set(), [], []
    for item in items:
        sid = item.get("id") if isinstance(item, dict) else None
        if sid not in by_id:
            extra.append(sid)
            continue
        if sid in seen:
            repeated.append(sid)
            continue
        seen.add(sid)
        validated.append((by_id[sid], *validate(item, by_id[sid])))
    if extra:
        log(f"  assess: ignored ids not in the batch: {extra}")
    if repeated:
        log(f"  assess: repeated ids, first result kept: {sorted(set(repeated))}")
    return result, validated, [s for s in stories if s.id not in seen]


def _assess_with_retry(
    conn: sqlite3.Connection | None,
    client,
    stories: list[StoryInput],
    today: date,
    publish_date: date | None,
    purpose: str,
    log,
):
    """The batch call, then one single-story call for each story the model left out:
    ([(story, fields, raw, call_id)], summed usage, calls, [stories still missing]).
    A story still missing stays unassessed (picked up next run); fails only when
    nothing in the batch was assessed."""
    result, validated, missing = _assess(conn, client, stories, today, publish_date, purpose, log)
    out = [(s, f, r, result.call_id) for s, f, r in validated]
    usage, calls = result.usage, 1
    still = []
    if missing:
        log(f"  assess: the model left out {[s.id for s in missing]}, asking again one by one")
    for story in missing:
        retry, got, gone = _assess(conn, client, [story], today, publish_date, purpose, log)
        calls += 1
        usage = usage + retry.usage
        out += [(s, f, r, retry.call_id) for s, f, r in got]
        still += gone
    if still:
        log(f"  assess: still no result for {[s.id for s in still]}; left unassessed for the next run")
    if not out:
        raise AssessError(f"assessment returned none of the batch {sorted(s.id for s in stories)}")
    return out, usage, calls, still


def assess_batch(
    conn: sqlite3.Connection,
    client,
    stories: list[StoryInput],
    today: date,
    publish_date: date | None = None,
    log=lambda message: None,
) -> tuple[LLMUsage, int, int]:
    """Assess and store one batch: (the token usage, stories assessed, calls)."""
    out, usage, calls, _ = _assess_with_retry(conn, client, stories, today, publish_date, "assess", log)
    with db.transaction(conn):
        for story, fields, raw, call_id in out:
            db.save_assessment(
                conn,
                story.id,
                fields,
                raw,
                conf().models.assessor.model,
                conf().steps.pick.assess_prompt_version,
                call_id,
            )
    return usage, len(out), calls


def assess_dry(
    conn: sqlite3.Connection | None,
    client,
    stories: list[StoryInput],
    today: date,
    publish_date: date,
    log=lambda message: None,
) -> tuple[dict[int, dict], LLMUsage]:
    """One batch assessed and validated as assess_batch() does, nothing stored but the
    calls' cost (purpose assess_supply): ({story id: fields}, the token usage). A story
    the model never assessed is absent from the dict."""
    out, usage, _, _ = _assess_with_retry(conn, client, stories, today, publish_date, "assess_supply", log)
    return {story.id: fields for story, fields, _, _ in out}, usage


def assess_stories(
    conn: sqlite3.Connection,
    client,
    source_names: dict[str, str],
    *,
    force: bool = False,
    story_ids: list[int] | None = None,
    today: date | None = None,
    publish_date: date | None = None,
    allow_archived: frozenset[int] = frozenset(),
    log=lambda message: print(message, flush=True),
) -> dict:
    """Assess every unassessed story (every story with force; or just story_ids), for a
    slot published on publish_date (pick.py passes the slot's day). allow_archived: archived
    tech candidates the weekly tech slot sends anyway (reelline/pipeline/techpick.py). Returns {"stories": n,
    "calls": n, "input": tokens, "output": tokens}."""
    if story_ids is None:
        story_ids = (
            [r[0] for r in conn.execute("SELECT id FROM stories ORDER BY id")]
            if force
            else db.unassessed_story_ids(conn)
        )
    today = today or datetime.now(UTC).date()
    rows = [db.get_story(conn, sid) for sid in story_ids]
    archived = [r["id"] for r in rows if r is not None and r["status"] == "archived" and r["id"] not in allow_archived]
    if archived:  # the tech news archive: never sent to a model, whoever asks
        log(f"  {len(archived)} archived stories left out (a source with assess: false)")
        rows = [r for r in rows if r is not None and (r["status"] != "archived" or r["id"] in allow_archived)]
    inputs = [story_input(r, source_names.get(r["source_id"], r["source_id"])) for r in rows if r is not None]
    totals = {"stories": 0, "calls": 0, "input": 0, "output": 0}
    size = conf().steps.pick.assess_batch
    for start in range(0, len(inputs), size):
        batch = inputs[start : start + size]
        usage, assessed, calls = assess_batch(conn, client, batch, today, publish_date, log=log)
        totals["stories"] += assessed
        totals["calls"] += calls
        totals["input"] += usage.input or 0
        totals["output"] += (usage.output or 0) + (usage.thinking or 0)
        log(
            f"  assessed {totals['stories']}/{len(inputs)} "
            f"({usage.input} in, {usage.output} out, {usage.thinking} thinking tokens)"
        )
    return totals
