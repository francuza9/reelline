"""The assess command: which stories, and what a re-assessment changed."""

from __future__ import annotations

import re
import sqlite3
from datetime import timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.assess import assess_stories
from reelline.pipeline.pick.base import PickError, assess_cost
from reelline.pipeline.pick.listing import next_slot
from reelline.pipeline.pick.picker import Picker
from reelline.pipeline.sources import load_sources


def select_stories(
    conn: sqlite3.Connection, grep: str | None, story_ids: list[int] | None, sources: list[str] | None = None
) -> list[int] | None:
    """Story ids matching --story, --grep (a regex, or a steps.pick.grep_presets name, over
    title and text, case-insensitive) and/or --source (the stories of these sources);
    None when none is given (all stories)."""
    if grep is None and not story_ids and not sources:
        return None
    pattern = None
    if grep is not None:
        try:
            pattern = re.compile(conf().steps.pick.grep_presets.get(grep, grep), re.IGNORECASE)
        except re.error as exc:
            raise PickError(f"--grep {grep!r} isn't a valid regex: {exc}") from None
    ids = []
    for row in conn.execute("SELECT id, title, text, source_id FROM stories ORDER BY id"):
        if story_ids and row["id"] not in story_ids:
            continue
        if sources and row["source_id"] not in sources:
            continue
        if pattern and not pattern.search(row["title"] + "\n" + row["text"]):
            continue
        ids.append(row["id"])
    return ids


def assessment_state(picker: Picker) -> dict[int, dict]:
    """Per assessed story: the assessment fields a re-assessment can change, and whether
    it's eligible for the next slot (with the gates if not)."""
    day, slot = next_slot(picker.now())
    r = picker.ranking(day, slot)
    eligible = {e.candidate.story_id for e in r.ranked}
    rows = {row["story_id"]: row for row in picker.conn.execute("SELECT * FROM assessments")}
    return {
        e.candidate.story_id: {
            "banned": bool(rows[e.candidate.story_id]["banned"]),
            "banned_reason": rows[e.candidate.story_id]["banned_reason"],
            "topic": e.candidate.topic,
            "policy_angle": rows[e.candidate.story_id]["policy_angle"],
            "base": e.candidate.base_score,
            "eligible": e.candidate.story_id in eligible,
            "gates": e.gates,
        }
        for e in r.evaluations
    }


def report_changes(before: dict[int, dict], after: dict[int, dict], ids: list[int], titles: dict[int, str]) -> None:
    changed = 0
    for sid in ids:
        old, new = before.get(sid), after.get(sid)
        if new is None:
            continue
        diffs = []
        if old is None:
            diffs.append("newly assessed")
        else:
            if old["banned"] != new["banned"]:
                diffs.append(
                    ("BANNED" if new["banned"] else "UNBANNED")
                    + f" ({new['banned_reason'] if new['banned'] else 'was: ' + str(old['banned_reason'])})"
                )
            if old["topic"] != new["topic"]:
                diffs.append(f"topic {old['topic']} -> {new['topic']}")
            if old["eligible"] != new["eligible"]:
                diffs.append("now ELIGIBLE" if new["eligible"] else "now OUT: " + "; ".join(new["gates"]))
            if abs(old["base"] - new["base"]) >= 1.5:
                diffs.append(f"base {old['base']:g} -> {new['base']:g}")
        if diffs:
            changed += 1
            print(f"  #{sid} {titles.get(sid, '')[:70]}\n      " + "; ".join(diffs))
    policy = sum(1 for sid in ids if after.get(sid, {}).get("policy_angle"))
    banned = sum(1 for sid in ids if after.get(sid, {}).get("banned"))
    print(
        f"{changed} of {len(ids)} changed (ban, topic, eligibility, or base by 1.5+); "
        f"now {banned} banned, {policy} with a policy angle"
    )


def cmd_assess(args, picker: Picker) -> int:
    conn = picker.conn
    if args.stale:
        since = db.now_iso(picker.now() - timedelta(days=conf().steps.pick.reassess_days))
        enabled = {
            src.id for src in load_sources(getattr(args, "registry", conf().sources)) if src.enabled and src.assess
        }
        ids = db.stale_story_ids(conn, conf().steps.pick.assess_prompt_version, since, enabled)
        if not ids:
            print("Nothing stale to re-assess.")
            return 0
        day, _ = next_slot(picker.now())
        print(
            f"Re-assessing {len(ids)} stories assessed under an older prompt (fetched since {since[:10]}), for "
            f"the {day.isoformat()} slot..."
        )
        totals = assess_stories(conn, picker.client_factory(), picker.source_names, story_ids=ids, publish_date=day)
        print(
            f"Assessed {totals['stories']} stories in {totals['calls']} calls: {totals['input']} input and "
            f"{totals['output']} output tokens, about ${assess_cost(totals):.4f}"
        )
        return 0
    ids = select_stories(conn, args.grep, args.story, args.source)
    if ids is None:
        ids = (
            [r[0] for r in conn.execute("SELECT id FROM stories ORDER BY id")]
            if args.force
            else db.unassessed_story_ids(conn)
        )
    elif not args.force:
        assessed = set(r[0] for r in conn.execute("SELECT story_id FROM assessments"))
        ids = [i for i in ids if i not in assessed]
    if not ids:
        print("Nothing to assess.")
        return 0
    print(
        f"Assessing {len(ids)} stories"
        + (f" matching {args.grep!r}" if args.grep else "")
        + (f" from {', '.join(args.source)}" if args.source else "")
        + "..."
    )
    before = assessment_state(picker) if args.force else {}
    totals = assess_stories(conn, picker.client_factory(), picker.source_names, story_ids=ids)
    print(
        f"Assessed {totals['stories']} stories in {totals['calls']} calls: {totals['input']} input and "
        f"{totals['output']} output tokens, about ${assess_cost(totals):.4f}"
    )
    if args.force:
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM stories")}
        report_changes(before, assessment_state(picker), ids, titles)
    return 0
