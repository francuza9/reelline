"""Shared pytest fixtures and their helpers (registered by conftest.py)."""

from types import SimpleNamespace

import pytest
from reelline.config import conf
from reelline.pipeline import caption as caption_mod
from reelline.pipeline import db

BRIEF = "# Brief: test\n\n- Topic: education\n\n## Facts\n\n- What it is: a competition.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    sid = db.insert_story(
        conn,
        db.NewStory(
            source_id="gtu_news",
            url="https://gtu.ge/a",
            canonical_url="https://gtu.ge/a",
            title="UNI4VERSE FINEDU",
            published_at="2026-09-30T12:00:00+00:00",
            fetched_at="2026-09-30T12:00:00+00:00",
            lang="ka",
            text="GITA competition source text. 20,000 GEL grant.",
            text_hash="h",
            word_count=300,
            topic_hint="education",
            date_source="feed",
        ),
    )
    fields = {k: None for k in db.ASSESSMENT_FIELDS}
    fields.update(
        banned=0,
        topic="education",
        base_score=8.7,
        same_program_key="uni4verse finedu 2026",
        summary="A competition",
        policy_angle=0,
    )
    db.save_assessment(conn, sid, fields, {}, "m", 2, None)
    pick_id = db.add_pick(conn, sid, "picked", "auto", [], 11.2, "2026-10-01", "13:00")
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO briefs (story_id, pick_id, path, created_at, model, prompt_version, markdown, raw_json)
                        VALUES (?, ?, 'x.md', ?, 'm', 2, ?, '{}')""",
            (sid, pick_id, db.now_iso(), BRIEF),
        )
    monkeypatch.setattr(conf().storage, "scripts_dir", tmp_path / "auto")
    monkeypatch.setattr(caption_mod, "REELS_DIR", tmp_path / "reels")
    return SimpleNamespace(conn=conn, story_id=sid, pick_id=pick_id, tmp=tmp_path)
