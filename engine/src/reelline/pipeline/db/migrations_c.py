"""Schema migrations 15-28. Shipped migrations are never edited."""

from reelline.pipeline.db.base import Migration

MIGRATIONS_C: tuple[Migration, ...] = (
    # Performance stats (reelline/pipeline/stats.py, read-only). post_stats: one row per
    # publication per fetch; as_of is when the numbers are from (Buffer's
    # metricsUpdatedAt, else the fetch), age_h the publication's age then, target_h the
    # snapshot point it fills (NULL: a sweep or the backfill), source the APIs merged
    # (buffer, graph, ig_graph, yt_data, yt_analytics); NULL where a platform gives
    # nothing; raw the parsed payloads (no secrets). account_stats: followers per
    # platform per Tbilisi day (the day's last fetch). stats_health: per source, the
    # last success, the failures in a row and whether that was alerted.
    # publications.native_id: the platform's own id (YouTube / TikTok video id, IG
    # media id, Facebook video id).
    Migration(
        15,
        "post_stats, account_stats, stats_health; publications.native_id",
        (
            """CREATE TABLE post_stats (
            id             INTEGER PRIMARY KEY,
            publication_id INTEGER NOT NULL REFERENCES publications(id),
            reel_id        INTEGER NOT NULL REFERENCES reels(id),
            channel        TEXT NOT NULL,
            fetched_at     TEXT NOT NULL,
            as_of          TEXT NOT NULL,
            age_h          REAL NOT NULL,
            target_h       REAL,
            source         TEXT NOT NULL,
            views          INTEGER,
            likes          INTEGER,
            comments       INTEGER,
            shares         INTEGER,
            saves          INTEGER,
            reach          INTEGER,
            follows        INTEGER,
            avg_watch_s    REAL,
            total_watch_s  REAL,
            avg_pct        REAL,
            pct_derived    INTEGER,
            raw            TEXT
        )""",
            "CREATE INDEX post_stats_pub ON post_stats(publication_id, as_of)",
            "CREATE INDEX post_stats_reel ON post_stats(reel_id)",
            """CREATE TABLE account_stats (
            platform   TEXT NOT NULL,
            day        TEXT NOT NULL,
            followers  INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            source     TEXT NOT NULL,
            PRIMARY KEY (platform, day)
        )""",
            """CREATE TABLE stats_health (
            source        TEXT PRIMARY KEY,
            ok_at         TEXT,
            failing_since TEXT,
            failures      INTEGER NOT NULL DEFAULT 0,
            error         TEXT,
            alerted       INTEGER NOT NULL DEFAULT 0,
            updated_at    TEXT NOT NULL
        )""",
            "ALTER TABLE publications ADD COLUMN native_id TEXT",
        ),
    ),
    # reels.opening_version: which reel opening (the config's render.opening_version) the
    # reel was rendered with, for /analytics' before/after split. Every reel before
    # this is 1; reelline/pipeline/produce.py stamps new ones.
    Migration(
        16,
        "reels.opening_version",
        (
            "ALTER TABLE reels ADD COLUMN opening_version INTEGER",
            "UPDATE reels SET opening_version = 1",
        ),
    ),
    # chains.mode: how a slot's chain runs (reelline/pipeline/run_daily.py). NULL: the same-day
    # chain (every chain before this); evening: the evening batch the day before (no
    # menu, the review held until steps.run_daily.evening_delivery_at); fallback: an evening
    # chain with no review by the old same-day start, run then (no menu, sent when ready).
    Migration(17, "chains.mode", ("ALTER TABLE chains ADD COLUMN mode TEXT",)),
    # Menu dismissals (reelline/pipeline/pick.py): a contender left out of a Confirm, or every
    # contender on "None of these", is dismissed for good (story status dismissed; one
    # row per story: reason, the menu and its slot, when; note: how it was recorded, e.g.
    # a backfill). pick_feedback: dismissals next to the reviewer's rejections, one
    # shape, for tuning assess later (kind rejection | dismissal; detail: a rejection's
    # next step).
    Migration(
        18,
        "dismissals; pick_feedback view",
        (
            """CREATE TABLE dismissals (
            id         INTEGER PRIMARY KEY,
            story_id   INTEGER NOT NULL UNIQUE REFERENCES stories(id),
            menu_id    INTEGER REFERENCES menus(id),
            reason     TEXT NOT NULL,
            slot_date  TEXT,
            slot_time  TEXT,
            by         TEXT,
            note       TEXT,
            created_at TEXT NOT NULL
        )""",
            "CREATE INDEX dismissals_menu ON dismissals(menu_id)",
            """CREATE VIEW pick_feedback AS
               SELECT 'rejection' AS kind, story_id, pick_id, reel_id, NULL AS menu_id, reason, next AS detail, by,
                      created_at FROM rejections
               UNION ALL
               SELECT 'dismissal', story_id, NULL, NULL, menu_id, reason, NULL, by, created_at FROM dismissals""",
        ),
    ),
    # jobs.step_at: when the job's current step started (reelline/pipeline/approve_bot.py run_job()),
    # for the per-step limit (steps.run_daily.step_max_min); NULL until its first step.
    Migration(19, "jobs.step_at", ("ALTER TABLE jobs ADD COLUMN step_at TEXT",)),
    # Education only (assess prompt v4, 2026-10-16): per education story, whether it's an
    # opportunity a viewer can still use (opportunity 0/1), its open status (open | upcoming
    # | closed | unclear), how many in Georgia can apply (reach: broad | medium | narrow),
    # its novelty (new | ongoing) and the event's own date (event_date, YYYY-MM-DD, kept
    # only with a quote from the text). base_score is computed from them (pipeline/
    # scoring.py education_base()). Rows assessed before are left as they are: pick
    # keeps them out until re-assessed.
    Migration(
        20,
        "assessments: opportunity, open_status, reach, novelty, event_date",
        (
            "ALTER TABLE assessments ADD COLUMN opportunity INTEGER",
            "ALTER TABLE assessments ADD COLUMN open_status TEXT",
            "ALTER TABLE assessments ADD COLUMN reach TEXT",
            "ALTER TABLE assessments ADD COLUMN novelty TEXT",
            "ALTER TABLE assessments ADD COLUMN event_date TEXT",
        ),
    ),
    # Shortlinks (reelline/pipeline/shortlinks.py): every <SHORTLINK_BASE>/<code> ever made, never
    # changed or deleted (every deploy of the site carries all of them): its target
    # (tracking parameters stripped), the reel it was made for and the kind (source |
    # credits), when, and when it was last checked live.
    Migration(
        21,
        "shortlinks",
        (
            """CREATE TABLE shortlinks (
            code        TEXT PRIMARY KEY,
            target      TEXT NOT NULL,
            reel_id     INTEGER REFERENCES reels(id),
            kind        TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            verified_at TEXT
        )""",
            "CREATE INDEX shortlinks_reel ON shortlinks(reel_id, kind)",
        ),
    ),
    # Link comments (reelline/pipeline/comments.py): why a publication's comment failed or waits, and
    # when it was posted (comment_state and comment_id are older).
    Migration(
        22,
        "publications.comment_error, comment_at",
        (
            "ALTER TABLE publications ADD COLUMN comment_error TEXT",
            "ALTER TABLE publications ADD COLUMN comment_at TEXT",
        ),
    ),
    # The proofread pass (reelline/pipeline/proofread.py): every run on a script or a caption hook, the
    # text before and after, whether it was used (applied) or kept the original (reason: the
    # guard that failed), and the word-level fixes, kept for tuning the prompts.
    Migration(
        23,
        "proofreads",
        (
            """CREATE TABLE proofreads (
            id             INTEGER PRIMARY KEY,
            script_id      INTEGER REFERENCES scripts(id),
            kind           TEXT NOT NULL,
            original       TEXT NOT NULL,
            proofread      TEXT,
            applied        INTEGER NOT NULL,
            fixes          TEXT NOT NULL,
            reason         TEXT,
            prompt_version INTEGER NOT NULL,
            created_at     TEXT NOT NULL
        )""",
            "CREATE INDEX proofreads_script ON proofreads(script_id, kind)",
        ),
    ),
    # picks.flirt: whether the pick's reel gets a flirt line (1/0), rolled once when its first
    # script is written (reelline/pipeline/script.py pick_flirt(); NULL before), kept by regenerations.
    Migration(24, "picks.flirt", ("ALTER TABLE picks ADD COLUMN flirt INTEGER",)),
    # picks.flirt_angle: the flirt line's angle (one of FLIRT_ANGLES), picked with the roll
    # (reelline/pipeline/script.py pick_flirt()); NULL without a flirt line.
    Migration(25, "picks.flirt_angle", ("ALTER TABLE picks ADD COLUMN flirt_angle TEXT",)),
    # Dropped connections (reelline/providers/retry.py): llm_calls.error is a failed try's error (NULL
    # for a call that answered), llm_calls.attempt the try (1 for a first-try answer; NULL
    # before this); scripts.passes each finished pass's output (JSON, reelline/pipeline/script.py
    # write_script()), so a retried or resumed script step goes on from the pass that failed.
    Migration(
        26,
        "llm_calls.error, attempt; scripts.passes",
        (
            "ALTER TABLE llm_calls ADD COLUMN error TEXT",
            "ALTER TABLE llm_calls ADD COLUMN attempt INTEGER",
            "ALTER TABLE scripts ADD COLUMN passes TEXT",
        ),
    ),
    # assessments.hook_strength (0-10, every topic) and tech_significance (0-10, tech only):
    # does the story have a concrete stake that can open a reel, and how big a tech story it
    # is (reelline/pipeline/assess.py, scoring.py). NULL before: such a story's score is unchanged.
    Migration(
        27,
        "assessments.hook_strength, tech_significance",
        (
            "ALTER TABLE assessments ADD COLUMN hook_strength INTEGER",
            "ALTER TABLE assessments ADD COLUMN tech_significance INTEGER",
        ),
    ),
    # The cold open (reelline/media/gags.py, channel docs/gags.md): reels.gag the gag the reel opens with (NULL: none),
    # gag_s its length (where the reel starts in the video; the Instagram / TikTok cover frame moves by
    # it), gag_mode how that was decided (remotion/src/gags.json mode ab | on | off, or forced: a
    # render's --gag); NULL for reels before it. The retention reports compare the A/B's (gag_mode ab)
    # reels with a gag and without.
    Migration(
        28,
        "reels.gag, gag_s, gag_mode",
        (
            "ALTER TABLE reels ADD COLUMN gag TEXT",
            "ALTER TABLE reels ADD COLUMN gag_s REAL",
            "ALTER TABLE reels ADD COLUMN gag_mode TEXT",
        ),
    ),
)
