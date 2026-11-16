"""Pipeline state database, no network: migrations on a fresh and an existing
database, transactions, source health (failure counting, auto-disable, reset),
stories and dropped URLs."""

import sqlite3

import pytest
from reelline.pipeline import db
from reelline.pipeline.db import DbError, Migration


def story(n: int = 1, **overrides) -> db.NewStory:
    fields = dict(
        source_id="src",
        url=f"https://ex.ge/a/{n}",
        canonical_url=f"https://ex.ge/a/{n}",
        title=f"Story {n}",
        published_at=None,
        fetched_at="2026-09-30T12:00:00+00:00",
        lang="ka",
        text=f"text {n}",
        text_hash=f"hash{n}",
        word_count=100,
        topic_hint="education",
    )
    fields.update(overrides)
    return db.NewStory(**fields)


def tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


# ---- Migrations ------------------------------------------------------------------------


def test_fresh_database_gets_every_migration(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert db.schema_version(conn) == db.MIGRATIONS[-1].version
    assert {"schema_version", "sources", "stories", "dropped", "runs"} <= tables(conn)
    rows = conn.execute("SELECT version, name, applied_at FROM schema_version").fetchall()
    assert [r["version"] for r in rows] == [m.version for m in db.MIGRATIONS]
    assert all(r["applied_at"] for r in rows)


def test_reopening_applies_nothing_and_keeps_data(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path)
    db.insert_story(conn, story())
    conn.close()
    conn = db.connect(path)
    assert db.migrate(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1


def test_new_migration_applies_to_an_existing_database(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path)
    db.insert_story(conn, story())
    conn.close()

    later = db.MIGRATIONS + (
        Migration(
            len(db.MIGRATIONS) + 1,
            "posts",
            (
                "CREATE TABLE posts (id INTEGER PRIMARY KEY, story_id INTEGER NOT NULL REFERENCES stories(id))",
                "INSERT INTO posts (story_id) SELECT id FROM stories",
            ),
        ),
    )
    conn = db.connect(path, later)
    assert db.schema_version(conn) == len(later)
    assert "posts" in tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1


def test_failed_migration_rolls_back_completely(tmp_path):
    path = tmp_path / "t.db"
    db.connect(path).close()
    broken = db.MIGRATIONS + (
        Migration(
            len(db.MIGRATIONS) + 1,
            "broken",
            (
                "CREATE TABLE half (id INTEGER)",
                "THIS IS NOT SQL",
            ),
        ),
    )
    with pytest.raises(DbError, match="broken"):
        db.connect(path, broken)
    conn = db.connect(path)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    assert "half" not in tables(conn)


def test_database_newer_than_code_fails(tmp_path):
    path = tmp_path / "t.db"
    later = db.MIGRATIONS + (Migration(len(db.MIGRATIONS) + 1, "future", ("CREATE TABLE future (id INTEGER)",)),)
    db.connect(path, later).close()
    with pytest.raises(DbError, match="newer than this code"):
        db.connect(path)


def test_migrations_must_be_numbered_in_order(tmp_path):
    gap = (Migration(1, "a", ("CREATE TABLE a (id INTEGER)",)), Migration(3, "c", ("CREATE TABLE c (id INTEGER)",)))
    with pytest.raises(DbError, match="numbered"):
        db.connect(tmp_path / "t.db", gap)


# ---- Transactions ----------------------------------------------------------------------


def test_transaction_rolls_back_on_error(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    with pytest.raises(RuntimeError), db.transaction(conn):
        db.ensure_source(conn, "a")  # joins the outer transaction
        raise RuntimeError("boom")
    assert db.get_source(conn, "a") is None
    assert not conn.in_transaction


def test_duplicate_canonical_url_fails_and_writes_nothing(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.insert_story(conn, story(1))
    with pytest.raises(DbError, match="Can't insert"):
        db.insert_story(conn, story(2, canonical_url="https://ex.ge/a/1"))
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1


def test_story_status_is_checked(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    sid = db.insert_story(conn, story())
    assert conn.execute("SELECT status FROM stories").fetchone()[0] == "new"
    db.set_story_status(conn, sid, "rejected", "off topic")
    assert tuple(conn.execute("SELECT status, status_reason FROM stories").fetchone()) == ("rejected", "off topic")
    with pytest.raises(DbError, match="Unknown story status"):
        db.set_story_status(conn, sid, "published")
    with pytest.raises(DbError, match="No story"):
        db.set_story_status(conn, sid + 1, "used")


# ---- Source health ---------------------------------------------------------------------


def test_failures_count_up_and_disable_at_the_limit(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert db.record_source_failure(conn, "s", "HTTP 500", max_failures=3) is False
    assert db.record_source_failure(conn, "s", "HTTP 502", max_failures=3) is False
    row = db.get_source(conn, "s")
    assert (row["consecutive_failures"], row["last_error"], row["disabled_reason"]) == (2, "HTTP 502", None)
    assert db.record_source_failure(conn, "s", "timeout", max_failures=3) is True
    row = db.get_source(conn, "s")
    assert row["consecutive_failures"] == 3
    assert "3 failures in a row" in row["disabled_reason"] and "timeout" in row["disabled_reason"]
    # Already disabled: no second "disabled now".
    assert db.record_source_failure(conn, "s", "again", max_failures=3) is False


def test_success_resets_the_failure_count(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.record_source_failure(conn, "s", "HTTP 500", max_failures=5)
    db.record_source_ok(conn, "s", '"etag-1"', "Wed, 30 Sep 2026 10:00:00 GMT")
    row = db.get_source(conn, "s")
    assert row["consecutive_failures"] == 0
    assert (row["etag"], row["last_modified"]) == ('"etag-1"', "Wed, 30 Sep 2026 10:00:00 GMT")
    assert row["last_ok_at"]


def test_reset_re_enables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.disable_source(conn, "s", "robots.txt disallows it")
    db.reset_source(conn, "s")
    row = db.get_source(conn, "s")
    assert (row["disabled_reason"], row["consecutive_failures"], row["last_error"]) == (None, 0, None)
    with pytest.raises(DbError, match="reason"):
        db.disable_source(conn, "s", "")


# ---- Known URLs, hashes, runs ----------------------------------------------------------


def test_known_url_and_hash(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.insert_story(conn, story(1, url="https://ex.ge/feed-link/1"))
    db.record_dropped(conn, "src", "https://ex.ge/short", "https://ex.ge/short-canonical", "too_short", "12 words")
    assert db.known_url(conn, "https://ex.ge/a/1") == "story"  # canonical
    assert db.known_url(conn, "https://ex.ge/feed-link/1") == "story"  # the feed's URL
    assert db.known_url(conn, "https://ex.ge/short") == "dropped"
    assert db.known_url(conn, "https://ex.ge/short-canonical") == "dropped"
    assert db.known_url(conn, "https://ex.ge/other") is None
    assert db.known_hash(conn, "hash1") and not db.known_hash(conn, "hash2")


def test_runs_record_counts_and_errors(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    run = db.start_run(conn, {"sources": ["a"]})
    db.finish_run(conn, run, "errors", {"a": {"new": 1}}, {"a": "HTTP 500"})
    row = db.last_runs(conn, 1)[0]
    assert (row["status"], row["counts"], row["errors"]) == ("errors", '{"a": {"new": 1}}', '{"a": "HTTP 500"}')
    with pytest.raises(DbError):
        db.finish_run(conn, run, "running", {}, {})


def test_naive_timestamps_are_refused():
    from datetime import datetime

    with pytest.raises(DbError, match="Naive"):
        db.now_iso(datetime(2026, 9, 30))


def test_foreign_keys_are_enforced(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO dropped (url, source_id, reason, dropped_at) VALUES ('u', 'nope', 'r', 't')")


def test_migration_2_gives_undated_stories_their_first_seen_time(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path, db.MIGRATIONS[:1])
    conn.execute("INSERT INTO sources (id) VALUES ('src')")
    for n, published in ((1, "2026-09-29T08:00:00+00:00"), (2, None)):
        conn.execute(
            """INSERT INTO stories (source_id, url, canonical_url, title, published_at, fetched_at, lang, text,
                   text_hash, word_count, topic_hint) VALUES ('src', ?, ?, 't', ?, '2026-09-30T20:00:00+00:00',
                   'ka', 'x', ?, 1, 'education')""",
            (f"u{n}", f"u{n}", published, f"h{n}"),
        )
    conn.close()
    conn = db.connect(path)
    rows = conn.execute("SELECT published_at, date_source FROM stories ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("2026-09-29T08:00:00+00:00", "unknown"),
        ("2026-09-30T20:00:00+00:00", "first_seen"),
    ]


def test_migration_16_marks_every_existing_reel_opening_v1(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path, db.MIGRATIONS[:15])
    conn.execute("PRAGMA foreign_keys = OFF")  # a bare reels row, without its pick and script
    for n in (1, 2):
        conn.execute(
            """INSERT INTO reels (pick_id, script_id, name, background, background_source, mood, status,
                            attempts, created_at, updated_at) VALUES (1, 1, ?, 'b', 'rotation', 'smug', 'published',
                            1, 'now', 'now')""",
            (f"r{n}",),
        )
    conn.close()
    conn = db.connect(path)
    assert [r[0] for r in conn.execute("SELECT opening_version FROM reels ORDER BY id")] == [1, 1]
