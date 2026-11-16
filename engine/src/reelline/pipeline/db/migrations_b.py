"""Schema migrations 11-14. Shipped migrations are never edited."""

from reelline.pipeline.db.base import Migration

MIGRATIONS_B: tuple[Migration, ...] = (
    # llm_calls becomes the one cost ledger (reelline/pipeline/costs.py reads it): every paid call,
    # TTS included, with the reel, pick and worker job it belongs to; manual: a dev CLI
    # (scripts/*.py, `qa run`); estimated: NULL for a measured cost, else how it was
    # estimated. The backfill (the rows before this): pick from the script or the brief;
    # reel for image / QA rows (the script's latest reel made by then: they were recorded
    # after the reel's insert); job by time window; the 23 assess rows logged before
    # migration 7 priced from their tokens; one estimated TTS row per reel.
    Migration(
        11,
        "llm_calls as the cost ledger: reel, pick, job, manual, estimated; backfill",
        (
            "ALTER TABLE llm_calls ADD COLUMN reel_id INTEGER REFERENCES reels(id)",
            "ALTER TABLE llm_calls ADD COLUMN pick_id INTEGER REFERENCES picks(id)",
            "ALTER TABLE llm_calls ADD COLUMN job_id INTEGER REFERENCES jobs(id)",
            "ALTER TABLE llm_calls ADD COLUMN manual INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE llm_calls ADD COLUMN estimated TEXT",
            "CREATE INDEX llm_calls_called_at ON llm_calls(called_at)",
            "CREATE INDEX llm_calls_script ON llm_calls(script_id)",
            """UPDATE llm_calls SET pick_id = (SELECT s.pick_id FROM scripts s WHERE s.id = llm_calls.script_id)
           WHERE script_id IS NOT NULL""",
            """UPDATE llm_calls SET pick_id = (SELECT b.pick_id FROM briefs b WHERE b.call_id = llm_calls.id)
           WHERE pick_id IS NULL AND id IN (SELECT call_id FROM briefs WHERE call_id IS NOT NULL)""",
            """UPDATE llm_calls SET reel_id = (
               SELECT r.id FROM reels r WHERE r.script_id = llm_calls.script_id AND r.created_at <= llm_calls.called_at
               ORDER BY r.id DESC LIMIT 1)
           WHERE script_id IS NOT NULL AND (purpose LIKE 'image%' OR purpose = 'qa_vision')""",
            """UPDATE llm_calls SET job_id = (
               SELECT j.id FROM jobs j WHERE j.pick_id = llm_calls.pick_id AND j.started_at IS NOT NULL
                   AND llm_calls.called_at >= j.started_at AND llm_calls.called_at <= COALESCE(j.finished_at, '9999')
               ORDER BY j.id DESC LIMIT 1)
           WHERE pick_id IS NOT NULL""",
            # models.gemini.price_usd_per_mtok when these were logged.
            """UPDATE llm_calls SET
               cost_usd = (COALESCE(input_tokens, 0) * 0.75
                           + (COALESCE(output_tokens, 0) + COALESCE(thinking_tokens, 0)) * 3.75) / 1000000.0,
               estimated = 'priced from tokens in migration 11 at $0.75 / $3.75 per 1M; thinking tokens were not '
                           || 'recorded, so a lower bound'
           WHERE cost_usd IS NULL AND model = 'gemini-3.8-flash'""",
            # reels.tts_cost_usd counts cached takes at their original cost, so a later reel
            # of the same script re-counts the takes it reused: it gets its increase over the
            # previous reel of the script; the first reel of a script, or a New voice reel
            # (every take fresh), its whole TTS cost.
            """INSERT INTO llm_calls (purpose, model, prompt_version, called_at, items, script_id, cost_usd,
               served_model, reel_id, pick_id, job_id, estimated)
           SELECT 'tts', 'gemini-3.8-flash-tts', 0, r.created_at, 1, r.script_id,
               CASE WHEN prev.id IS NULL OR j.kind = 'new_voice' THEN r.tts_cost_usd
                    ELSE MAX(r.tts_cost_usd - prev.tts_cost_usd, 0) END,
               'gemini-3.8-flash-tts', r.id, r.pick_id, j.id,
               'backfilled in migration 11 from reels.tts_cost_usd (cached takes counted once)'
           FROM reels r
           LEFT JOIN reels prev ON prev.id = (SELECT MAX(p.id) FROM reels p WHERE p.script_id = r.script_id
                                                 AND p.id < r.id AND p.tts_cost_usd IS NOT NULL)
           LEFT JOIN jobs j ON j.result = 'reel #' || r.id
           WHERE r.tts_cost_usd IS NOT NULL
           ORDER BY r.id""",
        ),
    ),
    # Publishing (reelline/pipeline/publish.py). publications: one per reel and channel (state,
    # the platform's post id, when it's due, mode scheduled | now, the public URL, the
    # error, the source comment's state and id). publish_calls: every API call (action,
    # method, endpoint, a redacted request summary, the HTTP status, the external id,
    # the error); never a secret or a signed URL's signature. jobs.detail: a job's
    # arguments (JSON). reels: the R2 keys uploaded for it, when it went live.
    Migration(
        12,
        "publications, publish_calls; jobs.detail; reels r2_keys, published_at",
        (
            """CREATE TABLE publications (
            id            INTEGER PRIMARY KEY,
            reel_id       INTEGER NOT NULL REFERENCES reels(id),
            channel       TEXT NOT NULL,
            state         TEXT NOT NULL,
            external_id   TEXT,
            due_at        TEXT,
            mode          TEXT,
            url           TEXT,
            error         TEXT,
            comment_state TEXT,
            comment_id    TEXT,
            attempts      INTEGER NOT NULL DEFAULT 0,
            checked_at    TEXT,
            published_at  TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            UNIQUE (reel_id, channel)
        )""",
            "CREATE INDEX publications_state ON publications(state)",
            """CREATE TABLE publish_calls (
            id          INTEGER PRIMARY KEY,
            reel_id     INTEGER,
            channel     TEXT,
            action      TEXT NOT NULL,
            method      TEXT NOT NULL,
            endpoint    TEXT NOT NULL,
            request     TEXT,
            status      INTEGER,
            external_id TEXT,
            error       TEXT,
            at          TEXT NOT NULL
        )""",
            "CREATE INDEX publish_calls_reel ON publish_calls(reel_id)",
            "ALTER TABLE jobs ADD COLUMN detail TEXT",
            "ALTER TABLE reels ADD COLUMN r2_keys TEXT",
            "ALTER TABLE reels ADD COLUMN published_at TEXT",
        ),
    ),
    # Image credits pages (reelline/pipeline/credits.py): each reel's images as published (title,
    # author, license, source), snapshotted from its images.json once, so its page
    # survives later renders of the same script; when it was last deployed.
    Migration(
        13,
        "credits",
        (
            """CREATE TABLE credits (
            reel_id     INTEGER PRIMARY KEY REFERENCES reels(id),
            images      TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            deployed_at TEXT,
            url         TEXT
        )""",
        ),
    ),
    # The daily schedule (reelline/pipeline/run_daily.py). jobs is rebuilt so pick_id may be NULL
    # (a "pick" job runs before there is a pick). chains: one per slot, the lock that
    # keeps overlapping ticks from running a second chain (pid + heartbeat of the tick
    # advancing it; a stale one is released and reported), its step and how many dead
    # workers it resumed. daily_tasks: detached work outside jobs (ingest), with its pid.
    # daily_events: things said once (the evening summary, a review sent, a late or
    # skipped slot, a stale lock, a stale bot).
    Migration(
        14,
        "jobs.pick_id nullable; chains, daily_tasks, daily_events",
        (
            """CREATE TABLE jobs_new (
            id          INTEGER PRIMARY KEY,
            kind        TEXT NOT NULL,
            slot_date   TEXT NOT NULL,
            slot_time   TEXT NOT NULL,
            pick_id     INTEGER REFERENCES picks(id),
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
            finished_at TEXT,
            detail      TEXT
        )""",
            """INSERT INTO jobs_new (id, kind, slot_date, slot_time, pick_id, review_id, story_fate, status, step, pid, error,
               result, attempts, message_id, created_at, started_at, finished_at, detail)
           SELECT id, kind, slot_date, slot_time, pick_id, review_id, story_fate, status, step, pid, error, result,
               attempts, message_id, created_at, started_at, finished_at, detail FROM jobs""",
            "DROP TABLE jobs",
            "ALTER TABLE jobs_new RENAME TO jobs",
            "CREATE INDEX jobs_slot ON jobs(slot_date, slot_time)",
            "CREATE INDEX jobs_status ON jobs(status)",
            """CREATE TABLE chains (
            id           INTEGER PRIMARY KEY,
            slot_date    TEXT NOT NULL,
            slot_time    TEXT NOT NULL,
            status       TEXT NOT NULL,
            step         TEXT,
            pid          INTEGER,
            heartbeat_at TEXT,
            resumes      INTEGER NOT NULL DEFAULT 0,
            note         TEXT,
            started_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            finished_at  TEXT,
            UNIQUE (slot_date, slot_time)
        )""",
            """CREATE TABLE daily_tasks (
            id          INTEGER PRIMARY KEY,
            kind        TEXT NOT NULL,
            status      TEXT NOT NULL,
            pid         INTEGER,
            error       TEXT,
            started_at  TEXT NOT NULL,
            finished_at TEXT
        )""",
            "CREATE INDEX daily_tasks_kind ON daily_tasks(kind, started_at)",
            """CREATE TABLE daily_events (
            kind   TEXT NOT NULL,
            key    TEXT NOT NULL,
            at     TEXT NOT NULL,
            detail TEXT,
            PRIMARY KEY (kind, key)
        )""",
        ),
        rebuild=True,
    ),
)
