"""Shared pytest fixtures and their helpers (registered by conftest.py)."""

from datetime import UTC, datetime

import pytest
from reelline.pipeline import db

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # 13:00 Tbilisi


def seed(
    conn,
    reel_id=1,
    published_at=T0,
    topic="education",
    duration=43.0,
    channels=("instagram", "tiktok", "youtube", "facebook"),
):
    at = db.now_iso(published_at)
    with db.transaction(conn):
        conn.execute("INSERT OR IGNORE INTO sources (id) VALUES ('src')")
        conn.execute(
            """INSERT INTO stories (id, source_id, url, canonical_url, title, fetched_at, lang, text, text_hash,
                            word_count, topic_hint) VALUES (?, 'src', ?, ?, ?, ?, 'ka', 't', ?, 100, 'x')""",
            (reel_id, f"u{reel_id}", f"u{reel_id}", f"Story {reel_id}", at, f"h{reel_id}"),
        )
        conn.execute(
            """INSERT INTO assessments (story_id, model, prompt_version, assessed_at, banned, topic, base_score,
                            same_program_key, summary, raw_json) VALUES (?, 'm', 1, ?, 0, ?, 5, 'k', 's', '{}')""",
            (reel_id, at, topic),
        )
        conn.execute(
            """INSERT INTO picks (id, story_id, status, chosen_by, candidates, created_at, updated_at)
                        VALUES (?, ?, 'posted', 'auto', '[]', ?, ?)""",
            (reel_id, reel_id, at, at),
        )
        conn.execute(
            """INSERT INTO scripts (id, pick_id, story_id, attempt, status, model, created_at, updated_at)
                        VALUES (?, ?, ?, 1, 'posted', 'm', ?, ?)""",
            (reel_id, reel_id, reel_id, at, at),
        )
        conn.execute(
            """INSERT INTO reels (id, pick_id, script_id, name, background, background_source, mood, status,
                            attempts, duration_s, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 'bg', 'rotation', 'calm', 'published', 1, ?, ?, ?)""",
            (reel_id, reel_id, reel_id, f"reel{reel_id}", duration, at, at),
        )
        urls = {
            "instagram": "https://www.instagram.com/reel/Dd9zZEEEVih/",
            "tiktok": "https://tiktok.com/@example_tv/video/7691804032662539541",
            "youtube": "https://www.youtube.com/watch?v=wh-e4UrVe04",
            "facebook": "https://www.facebook.com/reel/1103652055864996/",
        }
        ext = {"instagram": "b-ig", "tiktok": "b-tt", "youtube": "b-yt", "facebook": "fb1"}
        for ch in channels:
            conn.execute(
                """INSERT INTO publications (reel_id, channel, state, external_id, url, published_at, created_at,
                                updated_at) VALUES (?, ?, 'published', ?, ?, ?, ?, ?)""",
                (reel_id, ch, f"{ext[ch]}-{reel_id}" if reel_id != 1 else ext[ch], urls[ch], at, at, at),
            )


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "s.db")
    seed(c)
    return c
