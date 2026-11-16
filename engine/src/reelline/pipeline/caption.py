"""Caption fields for a script (Claude, prompts/caption.md, JSON schema) and the
assembled post caption.

write_caption() asks for hook (1-2 sentences in the host's voice, Georgian, no facts
beyond the brief), youtube_title (Georgian, at most steps.caption.youtube_title_max
characters) and hashtags (steps.caption.hashtags of them, including
steps.caption.required_hashtags). The answer is validated (sentence and hashtag counts,
title length, Georgian, emoji, no em/en dashes, no relative day words in the hook or
the title: the prompt has the slot's publish date, {{PUBLISH_DATE}}); a violation gets
one follow-up turn naming the problems, a second one fails. Then the hook gets the
proofread pass (reelline/pipeline/proofread.py; used only when its guards pass). Stored in the
captions table.

assemble_caption() builds the post text, in this order: the hook; a blank line;
the source line (steps.caption.source_line: "<source label>: <outlet> - <the reel's shortlink>",
reelline/pipeline/shortlinks.py link_for(), made on first use; a preview without a reel shows
"<SHORTLINK_BASE>/…"); one line linking the reel's image credits
page (reelline/pipeline/credits.py caption_line(): publish.credits_page.line) whenever the reel
shows any image (out/reels/<name>/images.json; no per-image lines, and the line is
always the 4th: the hook is one line); the SFX credits only with reelline/media/sfx/config.py
CREDITS["in_captions"]; the AI line (steps.caption.ai_line); the hashtags. Without a
reel (a script preview) the link shows "…" for the reel id. Platform limits (steps.caption.limits) are reelline/pipeline/qa.py's check: an
over-limit caption is still assembled, and the review says QA ❌.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.media.prompts import fill
from reelline.media.script_markup import relative_day_words
from reelline.media.sfx.config import CREDITS
from reelline.media.sfx.credits import split_attributions
from reelline.paths import REPO_ROOT
from reelline.pipeline import db
from reelline.pipeline.dates import georgian_day
from reelline.pipeline.script import _today, georgian_share, sentences
from reelline.providers import CostLedger, LLMError, get_llm

REELS_DIR = REPO_ROOT / "out" / "reels"
SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "youtube_title": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "youtube_title", "hashtags"],
    "additionalProperties": False,
}
_HASHTAG = re.compile(r"^#\w+$")
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿〰〽㊗㊙]")
_DASHES = ("—", "–")


class CaptionError(ReellineError, RuntimeError):
    """A caption that failed validation twice, a failed call, or missing caption fields."""


def emoji_count(text: str) -> int:
    return len(_EMOJI.findall(text))


def caption_problems(data: dict) -> list[str]:
    problems = []
    hook = " ".join(str(data.get("hook") or "").split())
    title = " ".join(str(data.get("youtube_title") or "").split())
    tags = data.get("hashtags")
    low, high = conf().steps.caption.hook_sentences
    n = len(sentences(hook))
    if not hook:
        problems.append("hook is empty")
    elif not low <= n <= high:
        problems.append(f"hook has {n} sentences; it must have {low}-{high}")
    if "#" in hook:
        problems.append("hook contains a hashtag")
    if hook and georgian_share(hook) < 0.6:
        problems.append("hook isn't Georgian")
    if not title:
        problems.append("youtube_title is empty")
    if len(title) > conf().steps.caption.youtube_title_max:
        problems.append(
            f"youtube_title is {len(title)} characters; the maximum is {conf().steps.caption.youtube_title_max}"
        )
    if title and georgian_share(title) < 0.6:
        problems.append("youtube_title isn't Georgian")
    if emoji_count(title) or "#" in title:
        problems.append("youtube_title has an emoji or hashtag")
    for field in ("hook", "youtube_title"):
        relative = relative_day_words(str(data.get(field) or ""))
        if relative:
            problems.append(
                f"{field} uses relative day words ({', '.join(dict.fromkeys(relative))}); the video is "
                "published on the date given: write the date itself or leave it out"
            )
    if emoji_count(hook) > conf().steps.caption.max_emoji:
        problems.append(f"hook has {emoji_count(hook)} emoji; at most {conf().steps.caption.max_emoji}")
    if any(d in hook + title for d in _DASHES):
        problems.append("em or en dash; use hyphens (-) only")
    low, high = conf().steps.caption.hashtags
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        problems.append("hashtags must be a list of strings")
        return problems
    if not low <= len(tags) <= high:
        problems.append(f"{len(tags)} hashtags; it must be {low}-{high}")
    bad = [t for t in tags if not _HASHTAG.match(t)]
    if bad:
        problems.append(f"malformed hashtags (a # then letters, digits or _ only): {', '.join(bad)}")
    lowered = [t.lower() for t in tags]
    for required in conf().steps.caption.required_hashtags:
        if required.lower() not in lowered:
            problems.append(f"hashtags must include {required}")
    if len({t.lower() for t in tags}) != len(tags):
        problems.append("duplicate hashtags")
    return problems


def normalize_fields(data: dict) -> dict:
    tags = [t.strip() for t in data["hashtags"]]
    canonical = {r.lower(): r for r in conf().steps.caption.required_hashtags}
    tags = [canonical.get(t.lower(), t) for t in tags]
    return {
        "hook": " ".join(data["hook"].split()),
        "youtube_title": " ".join(data["youtube_title"].split()),
        "hashtags": tags,
    }


def with_required_hashtags(tags: list[str]) -> tuple[list[str], list[str]]:
    """(tags with every steps.caption.required_hashtags entry missing from them appended,
    the ones appended); case-insensitive."""
    lowered = {t.lower() for t in tags}
    added = [r for r in conf().steps.caption.required_hashtags if r.lower() not in lowered]
    return list(tags) + added, added


def write_caption(conn: sqlite3.Connection, client, script_id: int) -> dict:
    from reelline.media.script_markup import parse
    from reelline.pipeline.script import display_text

    script = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
    if script is None or not script["final"]:
        raise CaptionError(f"Script #{script_id} has no final text")
    brief = conn.execute("SELECT markdown FROM briefs WHERE id = ?", (script["brief_id"],)).fetchone()
    parsed = parse(script["final"])
    pick = conn.execute("SELECT slot_date FROM picks WHERE id = ?", (script["pick_id"],)).fetchone()
    publish = date.fromisoformat(pick["slot_date"]) if pick is not None and pick["slot_date"] else _today()
    prompt = fill(
        conf().steps.caption.prompt.read_text(encoding="utf-8"),
        {
            "PUBLISH_DATE": georgian_day(publish),
            "TITLE_MAX": str(conf().steps.caption.youtube_title_max),
            "HASHTAGS_MIN": str(conf().steps.caption.hashtags[0]),
            "HASHTAGS_MAX": str(conf().steps.caption.hashtags[1]),
            "COVER": parsed.cover or "(none)",
            "NEWS_BRIEF": brief["markdown"],
            "SCRIPT": display_text(parsed),
        },
        conf().steps.caption.prompt.name,
    )
    messages = [{"role": "user", "content": prompt}]
    version = conf().steps.caption.prompt_version
    llm, ledger = get_llm("caption", client), CostLedger(conn, script_id=script_id)
    try:
        reply = llm.complete(messages, purpose="caption", prompt_version=version, ledger=ledger, schema=SCHEMA)
        data = _json(reply.text)
        problems = caption_problems(data)
        if problems:
            turn = "The caption fields have problems: " + "; ".join(problems) + ". Output the corrected JSON only."
            reply = llm.complete(
                llm.follow_up(messages, reply, turn),
                purpose="caption_retry",
                prompt_version=version,
                ledger=ledger,
                schema=SCHEMA,
            )
            data = _json(reply.text)
            problems = caption_problems(data)
            if problems:
                raise CaptionError("caption failed validation twice: " + "; ".join(problems))
    except LLMError as exc:
        raise CaptionError(str(exc)) from None
    fields = normalize_fields(data)
    from reelline.pipeline.proofread import proofread_hook
    from reelline.pipeline.proofread import save as save_proofread

    pr = proofread_hook(
        client, fields["hook"], fields["youtube_title"], fields["hashtags"], conn=conn, script_id=script_id
    )
    save_proofread(conn, script_id, pr)
    fields["hook"] = pr.text
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO captions (script_id, hook, youtube_title, hashtags, model, prompt_version, raw_json,
                   created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                script_id,
                fields["hook"],
                fields["youtube_title"],
                json.dumps(fields["hashtags"], ensure_ascii=False),
                reply.model or llm.spec.model,
                version,
                json.dumps(data, ensure_ascii=False),
                db.now_iso(),
            ),
        )
    return fields


def _json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CaptionError(f"caption isn't JSON ({exc}): {text[:200]!r}") from None
    if not isinstance(data, dict):
        raise CaptionError("caption JSON isn't an object")
    return data


def attributions_path(script_path: str | None) -> Path | None:
    """out/reels/<name>/attributions.txt for the script file (render_reel.py names the
    reel after the script's file stem)."""
    return REELS_DIR / Path(script_path).stem / "attributions.txt" if script_path else None


def images_json_path(script_path: str | None) -> Path | None:
    """out/reels/<name>/images.json for the script file (next to attributions.txt)."""
    return REELS_DIR / Path(script_path).stem / "images.json" if script_path else None


def assemble_caption(
    conn: sqlite3.Connection, script_id: int, source_names: dict[str, str], reel_id: int | None = None
) -> str:
    script = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
    if script is None:
        raise CaptionError(f"No script #{script_id}")
    cap = conn.execute("SELECT * FROM captions WHERE script_id = ? ORDER BY id DESC LIMIT 1", (script_id,)).fetchone()
    if cap is None:
        raise CaptionError(f"Script #{script_id} has no caption fields yet (write_caption)")
    from reelline.pipeline import shortlinks

    story = db.get_story(conn, script["story_id"])
    hashtags = json.loads(cap["hashtags"])
    if conf().publish.shortlinks.enabled:
        code = (
            shortlinks.link_for(conn, reel_id, "source", story["canonical_url"])["code"] if reel_id is not None else "…"
        )
        source_url = shortlinks.caption_url(code)
    else:  # no site to serve the redirect: the caption links the source itself
        source_url = story["canonical_url"]
    lines = [
        cap["hook"],
        "",
        conf().steps.caption.source_line.format(
            outlet=source_names.get(story["source_id"], story["source_id"]), url=source_url
        ),
    ]
    from reelline.pipeline.credits import CreditsError, caption_line, shown_images

    try:
        shown = shown_images(images_json_path(script["path"]))
    except CreditsError as exc:
        raise CaptionError(f"credits line: {exc}") from None
    if shown:  # any image on screen, whatever its license
        lines.append(caption_line(reel_id))
    attributions = attributions_path(script["path"])
    if CREDITS["in_captions"] and attributions is not None and attributions.is_file():
        lines += split_attributions(attributions.read_text(encoding="utf-8"))[1]
    lines += [conf().steps.caption.ai_line, " ".join(hashtags)]
    return "\n".join(lines)
