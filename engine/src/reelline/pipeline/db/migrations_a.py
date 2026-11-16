"""Schema migrations 1-10. Shipped migrations are never edited."""

from reelline.pipeline.db.base import Migration

MIGRATIONS_A: tuple[Migration, ...] = (
    Migration(
        1,
        "sources, stories, dropped, runs",
        (
            """CREATE TABLE sources (
            id                   TEXT PRIMARY KEY,
            last_ok_at           TEXT,
            last_error           TEXT,
            last_error_at        TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            disabled_reason      TEXT,
            disabled_at          TEXT,
            etag                 TEXT,
            last_modified        TEXT
        )""",
            """CREATE TABLE runs (
            id          INTEGER PRIMARY KEY,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            status      TEXT NOT NULL,
            args        TEXT NOT NULL,
            counts      TEXT,
            errors      TEXT
        )""",
            """CREATE TABLE stories (
            id            INTEGER PRIMARY KEY,
            source_id     TEXT NOT NULL REFERENCES sources(id),
            run_id        INTEGER REFERENCES runs(id),
            url           TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            title         TEXT NOT NULL,
            published_at  TEXT,
            fetched_at    TEXT NOT NULL,
            lang          TEXT NOT NULL,
            text          TEXT NOT NULL,
            text_hash     TEXT NOT NULL,
            word_count    INTEGER NOT NULL,
            status        TEXT NOT NULL DEFAULT 'new',
            status_reason TEXT,
            topic_hint    TEXT NOT NULL
        )""",
            "CREATE INDEX stories_url ON stories(url)",
            "CREATE INDEX stories_text_hash ON stories(text_hash)",
            "CREATE INDEX stories_status ON stories(status, fetched_at)",
            "CREATE INDEX stories_source ON stories(source_id, fetched_at)",
            """CREATE TABLE dropped (
            url           TEXT PRIMARY KEY,
            canonical_url TEXT,
            source_id     TEXT NOT NULL REFERENCES sources(id),
            reason        TEXT NOT NULL,
            detail        TEXT,
            dropped_at    TEXT NOT NULL
        )""",
            "CREATE INDEX dropped_canonical ON dropped(canonical_url)",
        ),
    ),
    # Where published_at came from: feed | listing | page | extractor | first_seen
    # (no date anywhere: the time ingest first saw it). Stories stored before this
    # have "unknown", or first_seen when they had no date.
    Migration(
        2,
        "stories.date_source; first-seen time for undated stories",
        (
            "ALTER TABLE stories ADD COLUMN date_source TEXT",
            "UPDATE stories SET date_source = 'unknown' WHERE published_at IS NOT NULL",
            "UPDATE stories SET published_at = fetched_at, date_source = 'first_seen' WHERE published_at IS NULL",
        ),
    ),
    # pick.py: one Gemini assessment per story (the fields of prompts/assess.md), and
    # every LLM call's token use (for cost).
    Migration(
        3,
        "assessments, llm_calls",
        (
            """CREATE TABLE llm_calls (
            id            INTEGER PRIMARY KEY,
            purpose       TEXT NOT NULL,
            model         TEXT NOT NULL,
            prompt_version INTEGER NOT NULL,
            called_at     TEXT NOT NULL,
            items         INTEGER NOT NULL,
            input_tokens  INTEGER,
            output_tokens INTEGER,
            thinking_tokens INTEGER
        )""",
            """CREATE TABLE assessments (
            story_id      INTEGER PRIMARY KEY REFERENCES stories(id),
            call_id       INTEGER REFERENCES llm_calls(id),
            model         TEXT NOT NULL,
            prompt_version INTEGER NOT NULL,
            assessed_at   TEXT NOT NULL,
            banned        INTEGER NOT NULL,
            banned_reason TEXT,
            topic         TEXT NOT NULL,
            edu_type      TEXT,
            provider      TEXT,
            provider_tier TEXT,
            cost          TEXT,
            marketing_or_low_credibility INTEGER,
            georgia_relevance TEXT,
            deadline      TEXT,
            who_can_apply TEXT,
            big_news      INTEGER,
            niche         INTEGER,
            base_score    REAL NOT NULL,
            same_program_key TEXT NOT NULL,
            summary       TEXT NOT NULL,
            raw_json      TEXT NOT NULL
        )""",
            "CREATE INDEX assessments_topic ON assessments(topic)",
        ),
    ),
    # pick.py: every pick decision, and the Telegram menus for close calls.
    Migration(
        4,
        "picks, menus",
        (
            """CREATE TABLE menus (
            id            INTEGER PRIMARY KEY,
            slot_date     TEXT NOT NULL,
            slot_time     TEXT NOT NULL,
            status        TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            deadline_at   TEXT NOT NULL,
            message_id    INTEGER,
            candidates    TEXT NOT NULL,
            selected      TEXT NOT NULL DEFAULT '[]',
            resolved_at   TEXT,
            resolved_by   TEXT
        )""",
            """CREATE TABLE picks (
            id              INTEGER PRIMARY KEY,
            story_id        INTEGER NOT NULL REFERENCES stories(id),
            slot_date       TEXT,
            slot_time       TEXT,
            status          TEXT NOT NULL,
            effective_score REAL,
            chosen_by       TEXT NOT NULL,
            candidates      TEXT NOT NULL,
            menu_id         INTEGER REFERENCES menus(id),
            queue_position  INTEGER,
            note            TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
            # One live pick per slot (queued and rejected picks don't hold one).
            """CREATE UNIQUE INDEX picks_slot ON picks(slot_date, slot_time)
               WHERE status NOT IN ('queued', 'rejected')""",
            "CREATE INDEX picks_story ON picks(story_id)",
        ),
    ),
    # brief.py: the fact brief for a picked story (the file in content/briefs/ too).
    Migration(
        5,
        "briefs",
        (
            """CREATE TABLE briefs (
            id             INTEGER PRIMARY KEY,
            story_id       INTEGER NOT NULL REFERENCES stories(id),
            pick_id        INTEGER REFERENCES picks(id),
            path           TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            model          TEXT NOT NULL,
            prompt_version INTEGER NOT NULL,
            call_id        INTEGER REFERENCES llm_calls(id),
            markdown       TEXT NOT NULL,
            raw_json       TEXT NOT NULL
        )""",
            "CREATE INDEX briefs_story ON briefs(story_id)",
        ),
    ),
    # assess prompt v2: regulation / policy stories are flagged (their briefs stick to
    # what changes and who it affects). NULL for stories assessed before.
    Migration(6, "assessments.policy_angle", ("ALTER TABLE assessments ADD COLUMN policy_angle INTEGER",)),
    # script.py: scripts per pick (draft, final, draft-lock report, fact-check), the
    # jokes punch-up inserted (recent lines), captions; llm_calls get the script they
    # belong to, their cost and the model that answered (a refusal fallback may differ).
    Migration(
        7,
        "scripts, jokes, captions; llm_calls script_id, cost_usd, served_model",
        (
            """CREATE TABLE scripts (
            id             INTEGER PRIMARY KEY,
            pick_id        INTEGER NOT NULL REFERENCES picks(id),
            story_id       INTEGER NOT NULL REFERENCES stories(id),
            brief_id       INTEGER REFERENCES briefs(id),
            attempt        INTEGER NOT NULL,
            status         TEXT NOT NULL,
            draft          TEXT,
            final          TEXT,
            draft_lock     TEXT,
            factcheck      TEXT,
            failures       TEXT,
            error          TEXT,
            model          TEXT NOT NULL,
            path           TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        )""",
            "CREATE INDEX scripts_pick ON scripts(pick_id)",
            """CREATE TABLE jokes (
            id         INTEGER PRIMARY KEY,
            script_id  INTEGER NOT NULL REFERENCES scripts(id),
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
            "CREATE INDEX jokes_script ON jokes(script_id)",
            """CREATE TABLE captions (
            id             INTEGER PRIMARY KEY,
            script_id      INTEGER NOT NULL REFERENCES scripts(id),
            hook           TEXT NOT NULL,
            youtube_title  TEXT NOT NULL,
            hashtags       TEXT NOT NULL,
            model          TEXT NOT NULL,
            prompt_version INTEGER NOT NULL,
            raw_json       TEXT NOT NULL,
            created_at     TEXT NOT NULL
        )""",
            "CREATE INDEX captions_script ON captions(script_id)",
            "ALTER TABLE llm_calls ADD COLUMN script_id INTEGER REFERENCES scripts(id)",
            "ALTER TABLE llm_calls ADD COLUMN cost_usd REAL",
            "ALTER TABLE llm_calls ADD COLUMN served_model TEXT",
        ),
    ),
    Migration(
        8,
        "reels",
        (
            # One row per produce run of a script. background_source: rotation | manual.
            # check_these: the aligner's low-score words (JSON). caption: the assembled
            # post caption. render_s: wall time of the render (both attempts).
            """CREATE TABLE reels (
            id                INTEGER PRIMARY KEY,
            pick_id           INTEGER NOT NULL REFERENCES picks(id),
            script_id         INTEGER NOT NULL REFERENCES scripts(id),
            name              TEXT NOT NULL,
            background        TEXT NOT NULL,
            background_source TEXT NOT NULL,
            mood              TEXT NOT NULL,
            status            TEXT NOT NULL,
            attempts          INTEGER NOT NULL,
            mp4_path          TEXT,
            cover_path        TEXT,
            images_path       TEXT,
            attributions_path TEXT,
            duration_s        REAL,
            check_these       TEXT,
            caption           TEXT,
            tts_cost_usd      REAL,
            error             TEXT,
            render_s          REAL,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )""",
            "CREATE INDEX reels_pick ON reels(pick_id)",
            "CREATE INDEX reels_script ON reels(script_id)",
        ),
    ),
    # QA (reelline/pipeline/qa.py): qa_status pass | fail | skipped (--no-qa); qa_failures and
    # qa_warnings are JSON lists of review lines; qa_path is the reel's qa.json. A reel
    # row whose pick got a newer rendered reel becomes status superseded.
    Migration(
        9,
        "reels QA",
        (
            "ALTER TABLE reels ADD COLUMN qa_status TEXT",
            "ALTER TABLE reels ADD COLUMN qa_failures TEXT",
            "ALTER TABLE reels ADD COLUMN qa_warnings TEXT",
            "ALTER TABLE reels ADD COLUMN qa_path TEXT",
        ),
    ),
    # The approval bot (reelline/pipeline/approve_bot.py, reelline/review/machine.py). reviews: one per
    # reel review sent to Telegram; message_id is the status-block message with the
    # buttons, text its text before any result lines (lines: JSON list), state the
    # button set showing (main | confirm_qa | confirm_late | regen | reject | reject_next
    # | caption | working). jobs: background work per slot (kind new_script |
    # new_voice | new_images | other_story | script_produce), run by a detached process
    # (pid); step is where it is or failed; message_id is its failure report (with the
    # Retry button). rejections: the reviewer's reject reasons,
    # for the script feedback loop. bot_state: the getUpdates offset. reels: who
    # approved and when, and when it publishes (publish_mode slot | now).
    Migration(
        10,
        "reviews, jobs, rejections, bot_state; reels approval",
        (
            """CREATE TABLE reviews (
            id                   INTEGER PRIMARY KEY,
            reel_id              INTEGER NOT NULL REFERENCES reels(id),
            pick_id              INTEGER NOT NULL REFERENCES picks(id),
            status               TEXT NOT NULL,
            state                TEXT NOT NULL,
            message_id           INTEGER,
            text                 TEXT NOT NULL,
            lines                TEXT NOT NULL DEFAULT '[]',
            reject_reason        TEXT,
            caption_requested_at TEXT,
            reminded_at          TEXT,
            decided_at           TEXT,
            decided_by           TEXT,
            sent_at              TEXT NOT NULL,
            updated_at           TEXT NOT NULL
        )""",
            "CREATE INDEX reviews_reel ON reviews(reel_id)",
            "CREATE INDEX reviews_status ON reviews(status)",
            """CREATE TABLE jobs (
            id          INTEGER PRIMARY KEY,
            kind        TEXT NOT NULL,
            slot_date   TEXT NOT NULL,
            slot_time   TEXT NOT NULL,
            pick_id     INTEGER NOT NULL REFERENCES picks(id),
            review_id   INTEGER REFERENCES reviews(id),
            story_fate  TEXT,
            status      TEXT NOT NULL,
            step        TEXT,
            pid         INTEGER,
            error       TEXT,
            result      TEXT,
            attempts    INTEGER NOT NULL DEFAULT 0,
            message_id  INTEGER,
            created_at  TEXT NOT NULL,
            started_at  TEXT,
            finished_at TEXT
        )""",
            "CREATE INDEX jobs_slot ON jobs(slot_date, slot_time)",
            "CREATE INDEX jobs_status ON jobs(status)",
            """CREATE TABLE rejections (
            id         INTEGER PRIMARY KEY,
            reel_id    INTEGER NOT NULL REFERENCES reels(id),
            pick_id    INTEGER NOT NULL REFERENCES picks(id),
            script_id  INTEGER NOT NULL REFERENCES scripts(id),
            story_id   INTEGER NOT NULL REFERENCES stories(id),
            reason     TEXT NOT NULL,
            next       TEXT NOT NULL,
            by         TEXT,
            created_at TEXT NOT NULL
        )""",
            """CREATE TABLE bot_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
            "ALTER TABLE reels ADD COLUMN approved_at TEXT",
            "ALTER TABLE reels ADD COLUMN approved_by TEXT",
            "ALTER TABLE reels ADD COLUMN publish_at TEXT",
            "ALTER TABLE reels ADD COLUMN publish_mode TEXT",
        ),
    ),
)
