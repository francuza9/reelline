"""Candidates, past picks and dismissals loaded from the database."""

from __future__ import annotations

import sqlite3
from datetime import date

from reelline.pipeline import db
from reelline.pipeline.pick.base import iso_ts, opt_bool
from reelline.pipeline.scoring import (
    Candidate,
    PastPick,
    slot_datetime,
)


def load_candidates(conn: sqlite3.Connection, story_ids: list[int] | None = None) -> list[Candidate]:
    """Every assessed story (or just story_ids) as a Candidate; never an archived one."""
    sql = "SELECT s.*, a.* FROM stories s JOIN assessments a ON a.story_id = s.id WHERE s.status != 'archived'"
    params: tuple = ()
    if story_ids is not None:
        sql += f" AND s.id IN ({', '.join('?' * len(story_ids))})"
        params = tuple(story_ids)
    out = []
    for r in conn.execute(sql + " ORDER BY s.id", params):
        out.append(
            Candidate(
                story_id=r["story_id"],
                title=r["title"],
                source_id=r["source_id"],
                canonical_url=r["canonical_url"],
                status=r["status"],
                published_at=iso_ts(r["published_at"]),
                date_source=r["date_source"],
                fetched_at=iso_ts(r["fetched_at"]),
                topic=r["topic"],
                banned=bool(r["banned"]),
                banned_reason=r["banned_reason"],
                base_score=r["base_score"],
                same_program_key=r["same_program_key"],
                summary=r["summary"],
                edu_type=r["edu_type"],
                provider=r["provider"],
                provider_tier=r["provider_tier"],
                cost=r["cost"],
                marketing_or_low_credibility=opt_bool(r["marketing_or_low_credibility"]),
                georgia_relevance=r["georgia_relevance"],
                deadline=date.fromisoformat(r["deadline"]) if r["deadline"] else None,
                who_can_apply=r["who_can_apply"],
                big_news=opt_bool(r["big_news"]),
                niche=opt_bool(r["niche"]),
                prompt_version=r["prompt_version"],
                opportunity=opt_bool(r["opportunity"]),
                open_status=r["open_status"],
                reach=r["reach"],
                novelty=r["novelty"],
                event_date=date.fromisoformat(r["event_date"]) if r["event_date"] else None,
                hook_strength=r["hook_strength"],
                tech_significance=r["tech_significance"],
            )
        )
    return out


def load_history(conn: sqlite3.Connection) -> list[PastPick]:
    rows = conn.execute(
        """SELECT p.*, s.canonical_url, s.title, a.same_program_key, a.topic, a.edu_type FROM picks p
           JOIN stories s ON s.id = p.story_id LEFT JOIN assessments a ON a.story_id = p.story_id"""
    ).fetchall()
    return [
        PastPick(
            story_id=r["story_id"],
            canonical_url=r["canonical_url"],
            same_program_key=r["same_program_key"],
            title=r["title"],
            topic=r["topic"],
            status=r["status"],
            at=slot_datetime(date.fromisoformat(r["slot_date"]), r["slot_time"])
            if r["slot_date"]
            else iso_ts(r["created_at"]),
            edu_type=r["edu_type"],
        )
        for r in rows
    ]


def load_dismissed(conn: sqlite3.Connection) -> list[PastPick]:
    """The stories dismissed in menus, as PastPicks (status "dismissed") for twin checks."""
    return [
        PastPick(
            story_id=r["story_id"],
            canonical_url=r["canonical_url"],
            same_program_key=r["same_program_key"],
            title=r["title"],
            topic=r["topic"],
            status="dismissed",
            at=iso_ts(r["created_at"]),
        )
        for r in db.dismissed(conn)
    ]
