"""The weekly big-tech slot's candidates: a cheap pre-filter, then a capped assessment.

Global tech sources are archived (sources.yaml assess: false) and never assessed. On the
pick run of the weekly tech slot (steps.pick.tech_weekday) shortlist() chooses, without any
model, up to the day's remaining steps.pick.tech_max_assess_per_day stories from the archived
stories of steps.pick.tech_sources fetched within steps.pick.tech_max_age_days of the slot:
  - a title with a big name (steps.pick.tech_big_names) first, then the source's priority
    (1 best), then the newest
  - a title like one already on the shortlist, or like a pick of the last
    steps.pick.repeat_days days (ratio steps.pick.tech_dedupe_fuzzy), or the same URL, is dropped
assess_tech() sends them to the assess prompt (the same call, batching and checks as any
story) and releases the assessed ones from the archive (status new), so they are ranked
like any candidate: scoring.evaluate() lets a tech story through on the tech slot only.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, date, datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.assess import assess_stories
from reelline.pipeline.scoring import similar, slot_datetime, tbilisi
from reelline.pipeline.sources import load_sources


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _big_name(title: str) -> bool:
    words = set(re.findall(r"[a-z0-9]+", title.casefold()))
    return any(name in words for name in conf().steps.pick.tech_big_names)


def remaining_today(conn: sqlite3.Connection, now: datetime) -> int:
    """How many more tech candidates may be assessed today (Tbilisi day)."""
    start = now.astimezone(tbilisi()).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    done = db.tech_assessed_since(conn, conf().steps.pick.tech_sources, db.now_iso(start))
    return max(0, conf().steps.pick.tech_max_assess_per_day - done)


def shortlist(conn: sqlite3.Connection, slot_day: date, now: datetime, registry=None) -> list[int]:
    """The story ids to assess for the tech slot of slot_day (module docstring)."""
    limit = remaining_today(conn, now)
    if not limit:
        return []
    slot_dt = slot_datetime(slot_day, conf().channel.slots[0])
    since = db.now_iso(min(now, slot_dt) - timedelta(days=conf().steps.pick.tech_max_age_days))
    priority = {s.id: s.priority for s in load_sources(registry)}
    rows = db.tech_candidate_rows(conn, conf().steps.pick.tech_sources, since)

    def ref(r) -> datetime:
        stamp = (
            _ts(r["fetched_at"])
            if r["date_source"] == "first_seen"
            else (_ts(r["published_at"]) or _ts(r["fetched_at"]))
        )
        return stamp or _ts(r["fetched_at"])

    rows = [r for r in rows if (slot_dt - ref(r)).total_seconds() / 86400 <= conf().steps.pick.tech_max_age_days]
    rows.sort(key=lambda r: (not _big_name(r["title"]), priority.get(r["source_id"], 3), -ref(r).timestamp(), r["id"]))
    recent = db.conn_recent_picks(conn, db.now_iso(now - timedelta(days=conf().steps.pick.repeat_days)))
    chosen: list[sqlite3.Row] = []
    for r in rows:
        if len(chosen) >= limit:
            break
        if any(
            r["canonical_url"] == o["canonical_url"]
            or similar(r["title"], o["title"]) >= conf().steps.pick.tech_dedupe_fuzzy
            for o in [*chosen, *recent]
        ):
            continue
        chosen.append(r)
    return [r["id"] for r in chosen]


def assess_tech(
    conn: sqlite3.Connection,
    client,
    source_names: dict[str, str],
    slot_day: date,
    now: datetime,
    log=print,
    registry=None,
) -> dict | None:
    """Shortlist, assess and release the tech candidates; assess_stories()' totals, None
    when there was nothing to assess."""
    ids = shortlist(conn, slot_day, now, registry)
    if not ids:
        log("Tech slot: no tech candidates to assess (none new, or today's cap reached)")
        return None
    log(f"Tech slot: assessing {len(ids)} tech candidates (cap {conf().steps.pick.tech_max_assess_per_day} a day)...")
    totals = assess_stories(
        conn, client, source_names, story_ids=ids, publish_date=slot_day, allow_archived=frozenset(ids), log=log
    )
    assessed = [
        r[0]
        for r in conn.execute(f"SELECT story_id FROM assessments WHERE story_id IN ({', '.join('?' * len(ids))})", ids)
    ]
    db.release_tech_stories(conn, assessed)
    return totals
