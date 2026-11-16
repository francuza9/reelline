"""The fact brief for a picked story: the input of the story pass (prompts/story.md,
scripts/fill_prompt.py story --brief <file>).

build_brief() asks Gemini (prompts/brief.md, JSON schema) for the facts the stored
text states, renders them as Markdown (what it is, what participants do or learn there,
who is behind it, what it's for; always the opportunity's details, DETAILS: who
can apply by age and by status, other requirements, the deadline, the cost, how and
where to apply, when it takes place; "not stated" when the text doesn't say) and saves the file to
content/briefs/<YYYY-MM-DD>_<slot>_<slug>.md (slot "1300" / "2000", or "manual"
for a story without a pick) plus a row in the briefs table. Any required field the
model leaves empty becomes "not stated". Key numbers whose digits don't appear in
the story text are flagged (printed and kept in the raw JSON) for a human to check.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.media.prompts import fill
from reelline.paths import REPO_ROOT
from reelline.pipeline import db
from reelline.pipeline.dates import georgian_day
from reelline.providers import CostLedger, LLMError, get_llm

NOT_STATED = "not stated"
# {{FRAMING}} in prompts/brief.md: policy stories (assessments.policy_angle) get the
# policy framing, the rest the default.
FRAMING_POLICY = (
    "This is a regulation or policy story. Keep the brief to what changes (the rule, ban, decision "
    "or agreement), from when, and who it affects and how. No political framing: leave out who "
    "supports or opposes it, political motives, reactions and quotes from politicians, and any "
    "party or country politics around it."
)
FRAMING_DEFAULT = "This is not a policy story; no special framing."
POLICY_HEADER = "- Angle: policy / regulation. Tell what changes and who it affects; no political framing."
# The opportunity's details, in the brief's order: (field, label). Always asked for and
# rendered (education only since 2026-10-16); "not stated" when the text doesn't say.
DETAILS = (
    ("who_can_apply_age", "Who can apply (age)"),
    ("who_can_apply_status", "Who can apply (status)"),
    ("requirements", "Requirements"),
    ("deadline", "Deadline"),
    ("cost", "Cost"),
    ("how_to_apply", "How to apply"),
    ("where_to_apply", "Where to apply"),
    ("when", "When it takes place"),
)

_TEXT = {"type": "string"}
_OPT = {"anyOf": [{"type": "string"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "what_it_is": _TEXT,
        "what_participants_do": _TEXT,
        "who_is_behind": _TEXT,
        "purpose": _TEXT,
        **{key: _OPT for key, _ in DETAILS},
        "key_numbers": {"type": "array", "items": _TEXT, "maxItems": conf().steps.brief.max_numbers},
        "caveats": {"type": "array", "items": _TEXT},
    },
}
SCHEMA["required"] = list(SCHEMA["properties"])


class BriefError(ReellineError, RuntimeError):
    """A brief that can't be built."""


@dataclass(frozen=True)
class Brief:
    path: Path
    markdown: str
    warnings: list[str]


def stated(value) -> str:
    """The field's text, or "not stated" when it's missing, empty or says so."""
    text = " ".join(str(value or "").split()).strip()
    if not text or text.rstrip(".").casefold() in (NOT_STATED, "none", "n/a", "unknown", "not mentioned"):
        return NOT_STATED
    return text


def slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "story"


def unsupported_numbers(bullets: list[str], text: str) -> list[str]:
    """Bullets holding a number whose digits don't appear in the text (1,200 = 1200 = 1 200)."""
    digits_in_text = re.sub(r"(?<=\d)[\s,.](?=\d{3}\b)", "", text)
    flagged = []
    for bullet in bullets:
        for number in re.findall(r"\d[\d,.\s]*\d|\d", bullet):
            plain = re.sub(r"(?<=\d)[\s,.](?=\d{3}\b)", "", number.strip())
            if plain not in digits_in_text:
                flagged.append(bullet)
                break
    return flagged


def render(data: dict, *, topic: str, title: str, source: str, url: str, published: str, policy: bool = False) -> str:
    lines = [
        f"# Brief: {title}",
        "",
        f"- Topic: {topic}",
        *([POLICY_HEADER] if policy else []),
        f"- Outlet: {source}",
        f"- Source URL: {url}",
        f"- {published}",
        "",
        "## Facts",
        "",
        f"- What it is: {stated(data.get('what_it_is'))}",
        f"- What participants do / learn: {stated(data.get('what_participants_do'))}",
        f"- Who is behind it: {stated(data.get('who_is_behind'))}",
        f"- What it's for / why it matters: {stated(data.get('purpose'))}",
    ]
    lines += [f"- {label}: {stated(data.get(key))}" for key, label in DETAILS]
    numbers = [stated(n) for n in data.get("key_numbers") or [] if stated(n) != NOT_STATED]
    caveats = [stated(c) for c in data.get("caveats") or [] if stated(c) != NOT_STATED]
    lines += ["", "## Key numbers", ""] + ([f"- {n}" for n in numbers] or [f"- {NOT_STATED}"])
    lines += ["", "## Caveats", ""] + ([f"- {c}" for c in caveats] or [f"- {NOT_STATED}"])
    return "\n".join(lines) + "\n"


def build_brief(
    conn: sqlite3.Connection,
    client,
    story_id: int,
    source_names: dict[str, str],
    pick_id: int | None = None,
    out_dir: Path | None = None,
    today: datetime | None = None,
) -> Brief:
    story = db.get_story(conn, story_id)
    if story is None:
        raise BriefError(f"No story {story_id}")
    if story["status"] == "archived":
        raise BriefError(f"Story {story_id} is archived (a source with assess: false): it never goes to a model")
    assessment = conn.execute("SELECT * FROM assessments WHERE story_id = ?", (story_id,)).fetchone()
    if assessment is None:
        raise BriefError(f"Story {story_id} isn't assessed yet (python -m reelline.pipeline.pick assess)")
    pick = conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone() if pick_id else None
    topic = assessment["topic"]
    policy = bool(assessment["policy_angle"])
    source = source_names.get(story["source_id"], story["source_id"])
    if story["date_source"] == "first_seen":
        published = f"First seen: {story['published_at'][:10]} (no publish date found)"
    else:
        published = f"Published: {(story['published_at'] or '')[:10] or NOT_STATED}"
    text = story["text"]
    if len(text) > conf().steps.brief.max_chars:
        text = text[: conf().steps.brief.max_chars] + " [...]"

    publish_day = (
        date.fromisoformat(pick["slot_date"])
        if pick is not None and pick["slot_date"]
        else (today or datetime.now(UTC)).date()
    )
    template = conf().steps.brief.prompt.read_text(encoding="utf-8")
    prompt = fill(
        template,
        {
            "PUBLISH_DATE": georgian_day(publish_day),
            "MAX_NUMBERS": str(conf().steps.brief.max_numbers),
            "FRAMING": FRAMING_POLICY if policy else FRAMING_DEFAULT,
            "TOPIC": topic,
            "SOURCE": source,
            "PUBLISHED": published,
            "TITLE": story["title"],
            "TEXT": text,
        },
        conf().steps.brief.prompt.name,
    )
    llm = get_llm("briefer", client)
    try:
        result = llm.complete_json(
            prompt,
            SCHEMA,
            purpose="brief",
            prompt_version=conf().steps.brief.prompt_version,
            ledger=CostLedger(conn, pick_id=pick_id),
        )
    except LLMError as exc:
        raise BriefError(str(exc)) from None
    data = result.data
    missing = [k for k in SCHEMA["required"] if k not in data]
    if missing:
        raise BriefError(f"Brief for story {story_id} lacks {', '.join(missing)}")
    if len(data.get("key_numbers") or []) > conf().steps.brief.max_numbers:
        raise BriefError(f"Brief for story {story_id} has more than {conf().steps.brief.max_numbers} key numbers")

    markdown = render(
        data,
        topic=topic,
        title=story["title"],
        source=source,
        url=story["canonical_url"],
        published=published,
        policy=policy,
    )
    warnings = [
        f"number not found in the text: {b}" for b in unsupported_numbers(data.get("key_numbers") or [], story["text"])
    ]
    raw = dict(data, _warnings=warnings) if warnings else data

    day = (
        pick["slot_date"]
        if pick is not None and pick["slot_date"]
        else ((today or datetime.now(UTC)).date().isoformat())
    )
    slot = pick["slot_time"].replace(":", "") if pick is not None and pick["slot_time"] else "manual"
    out_dir = out_dir or conf().storage.briefs_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day}_{slot}_{slugify(assessment['same_program_key'])}.md"
    path.write_text(markdown, encoding="utf-8")
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO briefs (story_id, pick_id, path, created_at, model, prompt_version, call_id, markdown,
                   raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                story_id,
                pick_id,
                str(shown),
                db.now_iso(),
                llm.spec.model,
                conf().steps.brief.prompt_version,
                result.call_id,
                markdown,
                json.dumps(raw, ensure_ascii=False, sort_keys=True),
            ),
        )
    return Brief(path, markdown, warnings)
