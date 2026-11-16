"""reelline/pipeline/costs.py, migration 11 and the cost recording it reads, no network: channel-time
month boundaries, an empty month, the approved vs waste split, superseded chains
(each cost counted once, under the right outcome), New script, failed and pending
spend, the step and provider groupings, the CSV columns, the current month's pace
estimate, the report total against a direct SQL sum, the migration's backfill,
produce recording TTS and failed attempts with the reel, the job context, manual
recording, and /costs in the bot."""

import csv
import io
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from reelline.config import conf
from reelline.media.tts import call_row as tts_row
from reelline.pipeline import costs, db
from reelline.pipeline.costs import Month, Row, World
from reelline.pipeline.scoring import tbilisi
from tests.fx_review import CHAT, FakeTg

OCT = Month(2026, 10)


def local_midnight(year, month):
    """The first of a month at 00:00 in the channel's time zone, as a UTC datetime."""
    return datetime(year, month, 1, tzinfo=UTC) - timedelta(hours=conf().channel.utc_offset_h)


NOW_OCT10 = datetime(2026, 10, 10, 12, 0, tzinfo=tbilisi())


@pytest.fixture
def october(monkeypatch):
    """The wall clock in October 2026 (the month these tests report on): rows stamped
    with db.now_iso() or reelline/providers/costs.py utc_now_iso() (cost rows) land in OCT whatever today's date is."""
    from reelline.providers import costs

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    monkeypatch.setattr(db, "datetime", Frozen)
    monkeypatch.setattr(costs, "datetime", Frozen)


def row(id, usd, purpose="story", model="claude-sonnet-5-5", at="2026-10-05T10:00:00+00:00", **kw):
    return Row(id=id, at=at, purpose=purpose, model=model, usd=usd, **kw)


def reel(pick_id, script_id, status, created_at="2026-10-05T09:00:00+00:00", name="r"):
    return {"pick_id": pick_id, "script_id": script_id, "status": status, "created_at": created_at, "name": name}


def report_of(rows, world, prev_total=0.0, now=NOW_OCT10, month=OCT):
    return costs.build_report(month, costs.attribute(rows, world), world, prev_total, now)


# ---- months --------------------------------------------------------------------------------


def test_month_parsing_and_arithmetic():
    assert costs.parse_month("2026-09") == Month(2026, 9)
    for bad in ("2026-9", "2026-13", "09-2026", "sept", "2026-00"):
        with pytest.raises(costs.CostsError):
            costs.parse_month(bad)
    assert Month(2026, 1).prev() == Month(2025, 12) and Month(2026, 12).next() == Month(2027, 1)
    assert Month(2026, 2).days() == 28 and OCT.days() == 31
    assert [m.key for m in costs.recent_months(NOW_OCT10, 12)][:3] == ["2026-10", "2026-09", "2026-08"]
    assert len(costs.recent_months(NOW_OCT10, 12)) == 12 and costs.recent_months(NOW_OCT10, 12)[-1].key == "2025-11"


def test_month_bounds_are_the_channels_midnight():
    assert OCT.bounds_utc() == (local_midnight(2026, 10).isoformat(), local_midnight(2026, 11).isoformat())
    # half an hour past the channel's midnight on 1 October is October, half an hour before it is September.
    assert costs.month_of(local_midnight(2026, 10) + timedelta(minutes=30)) == OCT
    assert costs.month_of(local_midnight(2026, 10) - timedelta(minutes=30)) == Month(2026, 9)


def test_a_call_after_the_channels_midnight_on_the_first_is_in_that_month(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    usage = {"input": 10, "output": 10, "thinking": 0}
    in_oct = (local_midnight(2026, 10) + timedelta(hours=3, minutes=30)).isoformat()
    in_sep = (local_midnight(2026, 10) - timedelta(seconds=1)).isoformat()
    in_nov = local_midnight(2026, 11).isoformat()
    db.record_llm_call(conn, "assess", "gemini-3.8-flash", 1, 1, usage, at=in_oct, cost_usd=1.0)
    db.record_llm_call(conn, "assess", "gemini-3.8-flash", 1, 1, usage, at=in_sep, cost_usd=2.0)
    db.record_llm_call(conn, "assess", "gemini-3.8-flash", 1, 1, usage, at=in_nov, cost_usd=4.0)
    assert [r.usd for r in costs.load_rows(conn, OCT)] == [1.0]
    assert [r.usd for r in costs.load_rows(conn, Month(2026, 9))] == [2.0]
    assert [r.usd for r in costs.load_rows(conn, Month(2026, 11))] == [4.0]
    r = costs.report(conn, OCT, NOW_OCT10)
    assert r.total == 1.0 and r.prev_total == 2.0
    assert "Total $1.00, -$1.00 (-50%) vs Sep 2026 ($2.00)" in costs.format_report(r)
    assert r.daily == [(datetime(2026, 10, 1).date(), 1.0)]


# ---- the report ----------------------------------------------------------------------------


def test_empty_month():
    r = report_of([], World(), prev_total=0.5, month=Month(2026, 9))
    text = costs.format_report(r)
    assert r.total == 0 and r.pace is None and r.per_approved is None
    assert "Costs: September 2026" in text and "No cost rows this month." in text
    assert "-$0.5000 (-100%) vs Aug 2026" in text
    assert text.endswith(conf().costs.footer)


def test_current_month_pace_estimate():
    r = report_of([row(1, 6.0), row(2, 4.0, at="2026-10-09T10:00:00+00:00")], World())
    assert r.pace == {"so_far": 10.0, "day": 10, "days": 31, "estimate": pytest.approx(31.0)}
    text = costs.format_report(r)
    assert "So far $10.00 in 10 of 31 days; pace estimate $31.00 for the month" in text
    assert report_of([row(1, 6.0)], World(), now=datetime(2026, 11, 2, tzinfo=UTC)).pace is None


def test_pace_projection_at_the_schedules_reels_a_day(monkeypatch):
    world = World(
        reels={1: reel(1, 1, "approved")},
        scripts={1: {"pick_id": 1, "status": "approved"}},
        picks={1: {"story_id": 11, "slot_date": "2026-10-06", "slot_time": "20:00", "status": "approved"}},
        titles={11: "Approved story"},
    )
    rows = [row(1, 3.0, script_id=1), row(2, 1.0, purpose="assess", model="gemini-3.8-flash")]
    monkeypatch.setattr(conf().channel, "slots", ("20:00",))
    r = report_of(rows, world)  # day 10 of 31; $4 for 1 approved reel
    assert r.pace["reels_per_day"] == 1 and r.pace["projection"] == pytest.approx(4.0 + 21 * 4.0)
    assert (
        "At 1 reel a day: $88.00 for the month (so far, then 21 days at $4.00 per approved reel)"
        in costs.format_report(r)
    )
    monkeypatch.setattr(conf().channel, "slots", ("13:00", "20:00"))
    assert report_of(rows, world).pace["projection"] == pytest.approx(4.0 + 21 * 2 * 4.0)


def test_waste_vs_approved_split():
    world = World(
        reels={
            1: reel(1, 1, "approved"),
            2: reel(2, 2, "rejected"),
            3: reel(4, 4, "expired"),
            4: reel(5, 5, "render_failed"),
        },
        scripts={
            1: {"pick_id": 1, "status": "approved"},
            2: {"pick_id": 2, "status": "ok"},
            4: {"pick_id": 4, "status": "ok"},
            5: {"pick_id": 5, "status": "ok"},
        },
        picks={
            1: {"story_id": 11, "slot_date": "2026-10-06", "slot_time": "13:00", "status": "approved"},
            2: {"story_id": 12, "slot_date": "2026-10-07", "slot_time": "13:00", "status": "rejected"},
            3: {"story_id": 13, "slot_date": "2026-10-08", "slot_time": "13:00", "status": "skipped"},
            4: {"story_id": 14, "slot_date": "2026-10-09", "slot_time": "13:00", "status": "approved"},
            5: {"story_id": 15, "slot_date": "2026-10-09", "slot_time": "20:00", "status": "render_failed"},
        },
        titles={11: "Approved story", 12: "Rejected story"},
    )
    rows = [
        row(1, 1.0, script_id=1),
        row(2, 0.5, purpose="tts", model="gemini-3.8-flash-tts", reel_id=1),
        row(3, 2.0, script_id=2),
        row(4, 0.25, purpose="brief", model="gemini-3.8-flash", pick_id=3),  # a skipped pick: no reel
        row(5, 0.75, script_id=4),
        row(6, 0.1, purpose="tts", model="gemini-3.8-flash-tts", reel_id=4),
        row(7, 3.0, purpose="assess", model="gemini-3.8-flash"),
    ]
    r = report_of(rows, world)
    assert r.outcome_usd == {"delivered": 1.5, "waste": pytest.approx(3.1), "overhead": 3.0}
    assert dict(r.waste) == {"rejected": 2.0, "expired": 0.75, "pick skipped or dropped": 0.25, "failed": 0.1}
    assert r.approved == 1 and r.per_approved == pytest.approx(7.6)  # all spend, waste and overhead included
    assert dict(r.reel_counts) == {
        "approved": 1,
        "published": 0,
        "rejected": 1,
        "superseded": 0,
        "expired": 1,
        "failed": 1,
        "in review": 0,
    }
    text = costs.format_report(r)
    assert "Approved / published $1.50 (20%)" in text and "Went nowhere $3.10 (41%)" in text
    assert "Overhead (assess) $3.00 (39%)" in text and "Cost per approved reel: $7.60 (all spend / 1 approved)" in text
    # A trial's calls (manual) are dev spend: shown as such, never part of a reel's cost.
    r = report_of(rows + [row(8, 2.0, purpose="punchup", manual=True)], world)
    assert r.total == pytest.approx(9.6) and r.manual == pytest.approx(2.0) and r.per_approved == pytest.approx(7.6)
    assert "Cost per approved reel: $7.60 (all spend / 1 approved; manual dev $2.00 left out)" in costs.format_report(r)
    assert "#2 Oct 05, slot Oct 07 13:00 (rejected): Rejected story, $2.00; biggest: Story $2.00" in text


def test_a_superseded_chain_counts_each_cost_once_under_the_right_outcome():
    """New voice twice on one script: reels 1 and 2 superseded, 3 approved. The script's
    calls and the brief count once, under reel 3; each reel's own render spend under it."""
    world = World(
        reels={1: reel(1, 1, "superseded"), 2: reel(1, 1, "superseded"), 3: reel(1, 1, "approved")},
        scripts={1: {"pick_id": 1, "status": "approved"}},
        picks={1: {"story_id": 1, "slot_date": "2026-10-06", "slot_time": "13:00", "status": "approved"}},
        jobs={7: {"kind": "new_voice", "status": "done"}, 8: {"kind": "new_voice", "status": "done"}},
    )
    tts = dict(purpose="tts", model="gemini-3.8-flash-tts")
    rows = [
        row(1, 0.25, purpose="brief", model="gemini-3.8-flash", pick_id=1),
        row(2, 1.0, script_id=1, pick_id=1),
        row(3, 0.5, reel_id=1, script_id=1, **tts),
        row(4, 0.5, reel_id=2, script_id=1, job_id=7, **tts),
        row(5, 0.5, reel_id=3, script_id=1, job_id=8, **tts),
        row(6, 0.125, purpose="qa_vision", model="gemini-3.8-flash", reel_id=3, script_id=1, job_id=8),
    ]
    attributed = costs.attribute(rows, world)
    assert [(c.outcome, c.reel_id) for c in attributed] == [
        ("delivered", 3),
        ("delivered", 3),
        ("waste", 1),
        ("waste", 2),
        ("delivered", 3),
        ("delivered", 3),
    ]
    r = costs.build_report(OCT, attributed, world, 0.0, NOW_OCT10)
    assert r.total == pytest.approx(2.875) and sum(r.outcome_usd.values()) == pytest.approx(r.total)
    assert r.outcome_usd == {"delivered": pytest.approx(1.875), "waste": 1.0}
    assert dict(r.waste) == {"superseded": 1.0}
    assert dict(r.steps)["Regenerations"] == pytest.approx(1.125)  # the two New voice jobs' calls
    assert dict(r.steps)["TTS (incl. re-rolls)"] == 0.5
    assert [(t["reel_id"], t["total"]) for t in r.top] == [(3, pytest.approx(1.875)), (1, 0.5), (2, 0.5)]
    assert r.top[0]["biggest"] == ("Story", 1.0)
    assert r.runs == 3 and r.median_run == 0.5  # each reel's own render spend


def test_new_script_leaves_the_old_script_as_waste_and_the_brief_with_the_last_reel():
    world = World(
        reels={1: reel(1, 1, "superseded"), 2: reel(1, 2, "approved")},
        scripts={1: {"pick_id": 1, "status": "ok"}, 2: {"pick_id": 1, "status": "approved"}},
        picks={1: {"story_id": 1, "slot_date": "2026-10-06", "slot_time": "13:00", "status": "approved"}},
    )
    rows = [
        row(1, 0.25, purpose="brief", model="gemini-3.8-flash", pick_id=1),
        row(2, 1.0, script_id=1),
        row(3, 2.0, script_id=2),
    ]
    assert [(c.outcome, c.detail, c.reel_id) for c in costs.attribute(rows, world)] == [
        ("delivered", "approved", 2),
        ("waste", "superseded", 1),
        ("delivered", "approved", 2),
    ]


def test_spend_without_a_reel_is_waste_or_pending():
    world = World(
        reels={},
        scripts={
            1: {"pick_id": 1, "status": "failed"},
            2: {"pick_id": 2, "status": "ok"},
            3: {"pick_id": 3, "status": "ok"},
            4: {"pick_id": 3, "status": "ok"},
        },
        picks={
            1: {"status": "scripted"},
            2: {"status": "scripted"},
            3: {"status": "scripted"},
            4: {"status": "picked"},
        },
        jobs={9: {"kind": "new_script", "status": "failed"}},
    )
    rows = [
        row(1, 1, script_id=1),
        row(2, 1, script_id=2, job_id=9),
        row(3, 1, script_id=3),
        row(4, 1, script_id=4),
        row(5, 1, purpose="brief", model="gemini-3.8-flash", pick_id=4),
        row(6, 1, purpose="mystery", model="gpt-x"),
    ]
    assert [(c.outcome, c.detail) for c in costs.attribute(rows, world)] == [
        ("waste", "script"),
        ("waste", "job"),
        ("waste", "replaced"),
        ("pending", "pending"),
        ("pending", "pending"),
        ("unattributed", "unlinked"),
    ]


def test_steps_providers_and_manual():
    world = World(jobs={1: {"kind": "script_produce", "status": "done"}})
    rows = [
        row(1, 1.0, purpose="punchup_retry"),
        row(2, 1.0, purpose="caption_fallback"),
        row(3, 1.0, purpose="image_generate_upper_bound", model="gemini-3.1-flash-image"),
        row(4, 1.0, purpose="tts_reroll", model="gemini-3.8-flash-tts"),
        row(5, 1.0, purpose="factcheck", model="gemini-3.8-flash", job_id=1),  # not a regeneration job
        row(6, 1.0, purpose="tts_tag", model="gemini-3.8-flash-tts", manual=True),
        row(7, 1.0, purpose="story", model="some-other-llm"),
    ]
    attributed = costs.attribute(rows, world)
    assert [c.step for c in attributed] == ["punchup", "caption", "images", "tts", "factcheck", "manual", "story"]
    assert [c.provider for c in attributed] == [
        "anthropic",
        "anthropic",
        "google",
        "google",
        "google",
        "google",
        "other",
    ]
    assert attributed[5].outcome == "manual"
    r = costs.build_report(OCT, attributed, world, 0.0, NOW_OCT10)
    assert dict(r.providers) == {"Anthropic": 2.0, "Google": 4.0, "Other": 1.0}
    assert dict(r.google) == {"Gemini text": 1.0, "TTS": 2.0, "image generation": 1.0}
    assert dict(r.steps)["Other (manual)"] == 1.0 and r.upper_bound
    text = costs.format_report(r)
    assert "Manual (dev) $1.00 (14%)" in text and "upper-bound price" in text


def test_csv_has_every_row_and_the_columns():
    world = World(
        reels={1: reel(1, 1, "approved")},
        scripts={1: {"pick_id": 1, "status": "approved"}},
        picks={1: {"story_id": 1, "slot_date": "2026-10-06", "slot_time": "13:00", "status": "approved"}},
    )
    rows = [
        row(1, 0.001234, script_id=1, tokens_in=100, tokens_out=20, tokens_thinking=5),
        row(2, None, purpose="assess", model="gemini-3.8-flash", estimated="from tokens"),
    ]
    text = costs.csv_text(costs.attribute(rows, world), world)
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert tuple(parsed[0]) == conf().costs.csv_columns
    for required in ("timestamp", "provider", "model", "step", "pick", "reel", "tokens_in", "tokens_out", "usd"):
        assert required in conf().costs.csv_columns
    assert len(parsed) == 2
    assert {
        k: parsed[0][k]
        for k in (
            "timestamp",
            "timestamp_tbilisi",
            "provider",
            "step",
            "pick",
            "slot",
            "reel",
            "outcome",
            "tokens_in",
            "tokens_out",
            "tokens_thinking",
            "usd",
        )
    } == {
        "timestamp": "2026-10-05T10:00:00+00:00",
        "timestamp_tbilisi": datetime(2026, 10, 5, 10, tzinfo=UTC).astimezone(tbilisi()).isoformat(),
        "provider": "Anthropic",
        "step": "Story",
        "pick": "1",
        "slot": "2026-10-06 13:00",
        "reel": "1",
        "outcome": "delivered (approved)",
        "tokens_in": "100",
        "tokens_out": "20",
        "tokens_thinking": "5",
        "usd": "0.001234",
    }
    assert (
        parsed[1]["usd"] == ""
        and parsed[1]["estimated"] == "from tokens"
        and parsed[1]["outcome"] == "overhead (assess)"
    )


def test_unpriced_and_estimated_rows_are_flagged_in_the_footer():
    rows = [row(1, None, model="claude-mystery"), row(2, 0.5, purpose="assess", estimated="x")]
    text = costs.format_report(report_of(rows, World()))
    assert "Includes $0.5000 estimated" in text and "Calls with no price, counted as $0: claude-mystery" in text


def test_dropped_tries_count_under_their_step_and_in_the_footer():
    rows = [
        row(1, 0.03, purpose="punchup", error="RemoteProtocolError: peer closed connection"),
        row(2, None, purpose="factcheck", model="gemini-3.8-flash", error="ReadTimeout: timed out"),
        row(3, 0.2, purpose="punchup"),
    ]
    r = report_of(rows, World())
    assert (r.failed_tries, r.failed_usd, r.unpriced) == (2, pytest.approx(0.03), [])
    assert dict(r.steps)["Punch-up"] == pytest.approx(0.23)
    text = costs.format_report(r)
    assert (
        "🔁 Retries: 2 tries dropped and tried again, $0.0300 (the tokens known when each dropped: a lower bound)"
        in text
    ) and "Calls with no price" not in text
    assert "Retries" not in costs.format_report(report_of([row(3, 0.2)], World()))


def test_a_dropped_try_reads_back_with_its_error(ready):
    w = ready
    db.record_llm_call(
        w.conn,
        "punchup",
        "claude-sonnet-5-5",
        1,
        1,
        {"input": 4000},
        at="2026-10-05T08:00:00+00:00",
        cost_usd=0.008,
        script_id=w.script_id,
        error="RemoteProtocolError: x",
        attempt=1,
    )
    report = costs.report(w.conn, OCT, NOW_OCT10)
    assert (report.failed_tries, report.failed_usd) == (1, pytest.approx(0.008))


# ---- migration 11 --------------------------------------------------------------------------


def test_migration_11_backfills_pick_reel_job_assess_and_tts(tmp_path):
    path = tmp_path / "m.db"
    conn = db.connect(path, db.MIGRATIONS[:10])
    t = "2026-10-01T10:00:00+00:00"
    conn.execute("INSERT INTO sources (id) VALUES ('s')")
    conn.execute(
        """INSERT INTO stories (id, source_id, url, canonical_url, title, fetched_at, lang, text, text_hash,
                    word_count, topic_hint) VALUES (1, 's', 'u', 'u', 'T', ?, 'ka', 'x', 'h', 1, 'edu')""",
        (t,),
    )
    conn.execute(
        """INSERT INTO picks (id, story_id, slot_date, slot_time, status, chosen_by, candidates, created_at,
                    updated_at) VALUES (1, 1, '2026-10-02', '13:00', 'approved', 'auto', '[]', ?, ?)""",
        (t, t),
    )
    conn.execute(
        """INSERT INTO llm_calls (id, purpose, model, prompt_version, called_at, items, input_tokens,
                    output_tokens) VALUES (1, 'assess', 'gemini-3.8-flash', 1, ?, 8, 1000000, 100000)""",
        (t,),
    )
    conn.execute(
        """INSERT INTO llm_calls (id, purpose, model, prompt_version, called_at, items, cost_usd)
                    VALUES (2, 'brief', 'gemini-3.8-flash', 1, ?, 1, 0.01)""",
        (t,),
    )
    conn.execute(
        """INSERT INTO briefs (id, story_id, pick_id, path, created_at, model, prompt_version, call_id,
                    markdown, raw_json) VALUES (1, 1, 1, 'b', ?, 'm', 1, 2, '', '{}')""",
        (t,),
    )
    conn.execute(
        """INSERT INTO scripts (id, pick_id, story_id, brief_id, attempt, status, model, created_at,
                    updated_at) VALUES (1, 1, 1, 1, 1, 'approved', 'm', ?, ?)""",
        (t, t),
    )
    for rid, status, created, tts in (
        (1, "superseded", "2026-10-01T11:00:00+00:00", 0.016),
        (2, "superseded", "2026-10-01T12:00:00+00:00", 0.023),
        (3, "approved", "2026-10-01T13:00:00+00:00", 0.020),
    ):
        conn.execute(
            """INSERT INTO reels (id, pick_id, script_id, name, background, background_source, mood, status,
                        attempts, tts_cost_usd, created_at, updated_at)
                        VALUES (?, 1, 1, 'n', 'b', 'rotation', 'smug', ?, 1, ?, ?, ?)""",
            (rid, status, tts, created, created),
        )
    conn.execute(
        """INSERT INTO jobs (id, kind, slot_date, slot_time, pick_id, status, result, created_at, started_at,
                    finished_at) VALUES (1, 'new_voice', '2026-10-02', '13:00', 1, 'done', 'reel #3', ?,
                    '2026-10-01T12:59:00+00:00', '2026-10-01T13:10:00+00:00')""",
        (t,),
    )
    for cid, at in ((3, "2026-10-01T11:05:00+00:00"), (4, "2026-10-01T13:05:00+00:00")):
        conn.execute(
            """INSERT INTO llm_calls (id, purpose, model, prompt_version, called_at, items, script_id,
                        cost_usd) VALUES (?, 'qa_vision', 'gemini-3.8-flash', 1, ?, 1, 1, 0.001)""",
            (cid, at),
        )
    conn.close()

    conn = db.connect(path)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    calls = {r["id"]: r for r in conn.execute("SELECT * FROM llm_calls")}
    assert calls[1]["cost_usd"] == pytest.approx(0.75 + 0.375) and "lower bound" in calls[1]["estimated"]
    assert calls[2]["pick_id"] == 1 and calls[2]["estimated"] is None
    assert (calls[3]["reel_id"], calls[3]["job_id"]) == (1, None)
    assert (calls[4]["reel_id"], calls[4]["job_id"], calls[4]["pick_id"]) == (3, 1, 1)
    tts = conn.execute(
        "SELECT reel_id, job_id, cost_usd, called_at FROM llm_calls WHERE purpose = 'tts' ORDER BY reel_id"
    ).fetchall()
    # reel 1 whole; reel 2 only its increase over reel 1 (the cached takes once); reel 3 New voice, whole.
    assert [(r[0], r[1], round(r[2], 6), r[3]) for r in tts] == [
        (1, None, 0.016, "2026-10-01T11:00:00+00:00"),
        (2, None, 0.007, "2026-10-01T12:00:00+00:00"),
        (3, 1, 0.020, "2026-10-01T13:00:00+00:00"),
    ]


# ---- recording -----------------------------------------------------------------------------


def test_cost_context_tags_job_and_manual_and_derives_the_pick(ready):
    w = ready
    usage = {"input": 1, "output": 1, "thinking": 0}
    with db.cost_context(job_id=None, manual=True):
        manual_id = db.record_llm_call(w.conn, "x", "m", 1, 1, usage, script_id=w.script_id)
    job = w.conn.execute(
        """INSERT INTO jobs (kind, slot_date, slot_time, pick_id, status, created_at)
                            VALUES ('new_voice', '2026-10-01', '13:00', ?, 'running', ?)""",
        (w.pick_id, db.now_iso()),
    ).lastrowid
    with db.cost_context(job_id=job):
        job_row = db.record_llm_call(w.conn, "y", "m", 1, 1, usage)
    plain = db.record_llm_call(w.conn, "z", "m", 1, 1, usage)
    got = {r["id"]: r for r in w.conn.execute("SELECT * FROM llm_calls")}
    assert (got[manual_id]["manual"], got[manual_id]["pick_id"], got[manual_id]["job_id"]) == (1, w.pick_id, None)
    assert (got[job_row]["job_id"], got[job_row]["manual"]) == (job, 0)
    assert (got[plain]["job_id"], got[plain]["manual"]) == (None, 0)


def test_record_manual_never_raises(tmp_path):
    logged = []
    db.record_manual([tts_row(10, 10, "tts_tag")], db_path=tmp_path, log=logged.append)  # a directory: can't open
    assert logged and logged[0].startswith("WARNING: 1 paid call(s)")
    db.record_manual([tts_row(1000, 1000, "tts_tag")], db_path=tmp_path / "ok.db", log=logged.append)
    conn = db.connect(tmp_path / "ok.db")
    assert conn.execute("SELECT purpose, manual, cost_usd FROM llm_calls").fetchone()[:] == (
        "tts_tag",
        1,
        pytest.approx((1000 * 0.5 + 1000 * 9.0) / 1e6),
    )


def _tts_renderer(base, tts_calls, fail_with=()):
    """tests/test_produce.py FakeRenderer whose renders also made TTS calls; the first
    len(fail_with) attempts fail, each having spent its list of calls."""
    from reelline.pipeline.produce import RenderFailed

    fails = list(fail_with)

    def render(script_path, background, mood, regen=(), **options):
        if fails:
            base.calls.append((script_path.name, background, mood, list(regen)))
            raise RenderFailed("remotion died", fails.pop(0))
        r = base(script_path, background, mood, regen, **options)
        r.tts_calls = [dict(c) for c in tts_calls]
        return r

    return render


def test_produce_records_tts_and_a_failed_attempt_with_the_reel(ready):
    from reelline.pipeline.produce import produce
    from tests.test_produce import NAMES, FakeChecker, FakeRenderer, FakeReview

    w = ready
    lost = [tts_row(50, 500)]
    renderer = _tts_renderer(FakeRenderer(), [tts_row(100, 1000), tts_row(80, 900)], fail_with=[lost])
    result = produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=renderer,
        review_factory=FakeReview,
        log=lambda m: None,
        checker_factory=lambda: FakeChecker(),
    )
    rows = w.conn.execute(
        "SELECT purpose, reel_id, pick_id, script_id, cost_usd FROM llm_calls WHERE purpose = 'tts' ORDER BY id"
    ).fetchall()
    assert [tuple(r)[:4] for r in rows] == [("tts", result.reel_id, w.pick_id, w.script_id)] * 3
    assert sum(r["cost_usd"] for r in rows) == pytest.approx(
        sum(c["cost_usd"] for c in lost + [tts_row(100, 1000), tts_row(80, 900)])
    )


def test_produce_records_both_failed_attempts_with_the_render_failed_reel(october, ready):
    from reelline.pipeline.produce import produce
    from tests.test_produce import NAMES, FakeRenderer, FakeReview

    w = ready
    renderer = _tts_renderer(FakeRenderer(), [], fail_with=[[tts_row(10, 10)], [tts_row(20, 20)]])
    result = produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=renderer,
        review_factory=FakeReview,
        log=lambda m: None,
        alert=False,
    )
    assert result.status == "render_failed"
    assert w.conn.execute(
        "SELECT COUNT(*), MIN(reel_id), MAX(reel_id) FROM llm_calls WHERE purpose = 'tts'"
    ).fetchone()[:] == (2, result.reel_id, result.reel_id)
    world = costs.load_world(w.conn)
    assert {
        (c.outcome, c.detail) for c in costs.attribute(costs.load_rows(w.conn, OCT), world) if c.row.purpose == "tts"
    } == {("waste", "render_failed")}


def test_qa_reroll_takes_are_recorded_as_tts_reroll(ready):
    from reelline.pipeline.produce import produce
    from tests.test_produce import LOW_ROW, NAMES, FakeReview, RerollRenderer, old_take, stem_of

    w = ready
    old_take(w, stem_of(w))
    base = RerollRenderer(rows=[LOW_ROW], reroll_rows=[])

    def renderer(script_path, background, mood, regen=(), **options):
        r = base(script_path, background, mood, regen, **options)
        r.tts_calls = [tts_row(10, 100)] if regen else [tts_row(100, 1000)]
        return r

    produce(w.conn, "2026-10-01", "13:00", NAMES, renderer=renderer, review_factory=FakeReview, log=lambda m: None)
    assert [r[0] for r in w.conn.execute("SELECT purpose FROM llm_calls WHERE purpose LIKE 'tts%' ORDER BY id")] == [
        "tts",
        "tts_reroll",
    ]


def test_report_total_matches_the_sql_sum(ready):
    w = ready
    usage = {"input": 1, "output": 1, "thinking": 0}
    for i, at in enumerate(("2026-10-01T08:00:00+00:00", "2026-10-15T08:00:00+00:00", "2026-09-30T19:00:00+00:00")):
        db.record_llm_call(
            w.conn, "story", "claude-sonnet-5-5", 1, 1, usage, at=at, cost_usd=0.1 * (i + 1), script_id=w.script_id
        )
    costs_, _ = costs.month_costs(w.conn, OCT)
    assert sum(c.usd for c in costs_) == pytest.approx(costs.sql_total(w.conn, OCT))
    assert costs.report(w.conn, OCT, NOW_OCT10).total == pytest.approx(costs.sql_total(w.conn, OCT))


def test_cli_report_and_csv(ready, capsys, tmp_path):
    w = ready
    db.record_llm_call(
        w.conn,
        "story",
        "claude-sonnet-5-5",
        1,
        1,
        {"input": 1, "output": 1, "thinking": 0},
        at="2026-09-10T08:00:00+00:00",
        cost_usd=0.25,
        script_id=w.script_id,
    )
    path = w.conn.execute("PRAGMA database_list").fetchone()["file"]
    assert costs.main(["--db", path, "report", "2026-09"]) == 0
    assert "Costs: September 2026" in capsys.readouterr().out
    out = tmp_path / "c.csv"
    assert costs.main(["--db", path, "csv", "2026-09", "-o", str(out)]) == 0
    assert len(out.read_text().splitlines()) == 2
    assert costs.main(["--db", path, "total", "2026-09"]) == 0
    assert "report total $0.250000 (1 rows), SQL SUM(cost_usd) $0.250000" in capsys.readouterr().out
    assert costs.main(["--db", path, "report", "2026-9"]) == 2


# ---- /costs in the bot ---------------------------------------------------------------------


class DocTg(FakeTg):
    def __init__(self):
        super().__init__()
        self.documents = []

    def send_document(self, filename, data, caption=None, mime="text/csv"):
        self.documents.append(SimpleNamespace(filename=filename, data=data, caption=caption))
        return {"message_id": 1}


@pytest.fixture
def cb(b):
    b.tg = b.bot.tg = DocTg()
    b.clock["now"] = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    return b


def command(w, text, update_id=10):
    w.bot.handle({"update_id": update_id, "message": {"text": text, "chat": {"id": int(CHAT)}, "from": {}}})


def press(w, data, update_id=11):
    w.bot.handle(
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"cb{update_id}",
                "data": data,
                "from": {"username": "u"},
                "message": {"message_id": 77, "chat": {"id": int(CHAT)}},
            },
        }
    )


def test_costs_command_buttons_and_month_picker(cb):
    command(cb, "/costs")
    msg = cb.tg.sent[-1]
    assert msg.text == "API costs: which month?"
    assert cb.tg.buttons(msg.markup) == ["This month", "Last month", "Pick month"]
    press(cb, "c:pick")
    edit = cb.tg.last_edit(77)
    months = [b["callback_data"] for row in edit.markup["inline_keyboard"] for b in row]
    assert len(months) == 12 and months[0] == "c:m:2026-10" and months[-1] == "c:m:2025-11"


def test_costs_report_with_the_csv_button_and_the_csv(october, cb):
    total = cb.conn.execute("SELECT SUM(cost_usd) FROM llm_calls").fetchone()[0]
    press(cb, "c:m:this")
    report = cb.tg.sent[-1]
    assert report.text.startswith("💸 Costs: October 2026") and f"Total {costs.usd(total)}" in report.text
    assert report.markup["inline_keyboard"][-1][0]["callback_data"] == "c:csv:2026-10"
    press(cb, "c:csv:2026-10", update_id=12)
    (doc,) = cb.tg.documents
    assert doc.filename == f"{conf().channel.name}_costs_2026-10.csv"
    lines = doc.data.decode().splitlines()
    assert (
        lines[0] == ",".join(conf().costs.csv_columns)
        and len(lines) - 1 == cb.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    )
    press(cb, "c:m:last", update_id=13)
    assert cb.tg.sent[-1].text.startswith("💸 Costs: September 2026")


def test_costs_command_with_a_month_and_a_bad_one(cb):
    command(cb, "/costs 2026-09")
    assert cb.tg.sent[-1].text.startswith("💸 Costs: September 2026")
    command(cb, "/costs septembre", update_id=12)
    assert "Not a month" in cb.tg.sent[-1].text
    press(cb, "c:zzz", update_id=13)
    assert cb.tg.answers[-1] == "Unknown button"


def test_a_long_report_is_split_with_the_csv_button_on_the_last_part(cb, monkeypatch):
    monkeypatch.setattr(costs, "format_report", lambda r: "x" * 3000 + "\n" + "y" * 3000)
    command(cb, "/costs 2026-10")
    first, last = cb.tg.sent[-2:]
    assert first.markup is None and len(first.text) == 3000
    assert last.markup["inline_keyboard"][0][0]["callback_data"] == "c:csv:2026-10"


def test_a_worker_job_tags_its_calls(cb):
    usage = {"input": 1, "output": 1, "thinking": 0}

    class Steps:
        def produce(self, slot_date, slot_time, **kw):
            db.record_llm_call(cb.conn, "tts", "gemini-3.8-flash-tts", 0, 1, usage, cost_usd=0.01)
            return SimpleNamespace(status="rendered", reel_id=cb.reel_id)

    from reelline.pipeline import approve_bot as ab

    pick = cb.conn.execute("SELECT * FROM picks WHERE id = ?", (cb.pick_id,)).fetchone()
    job_id = ab.add_job(cb.conn, "new_voice", pick, cb.review["id"])
    assert ab.run_job(cb.conn, job_id, cb.tg, Steps(), log=lambda m: None) == "done"
    assert cb.conn.execute("SELECT job_id FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()[0] == job_id
    assert (
        db.record_llm_call(cb.conn, "x", "m", 1, 1, usage)
        and cb.conn.execute("SELECT job_id FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()[0] is None
    )


def test_send_document_is_multipart():
    import httpx
    from reelline.notify.telegram import TelegramBot

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    settings = SimpleNamespace(bot_token="T0K", chat_id=CHAT)
    bot = TelegramBot(settings, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert bot.send_document("c.csv", b"a,b\n1,2\n", caption="rows") == {"message_id": 5}
    (req,) = seen
    body = req.content.decode()
    assert req.url.path.endswith("/sendDocument") and "multipart/form-data" in req.headers["content-type"]
    assert 'name="chat_id"' in body and CHAT in body and 'filename="c.csv"' in body and "a,b\n1,2" in body


# ---- the review's "Cost so far" ----------------------------------------------------------


def _cost_total(line: str) -> float:
    return float(line.split("Cost so far: $", 1)[1].split(" ", 1)[0])


def test_review_cost_line_matches_the_report_for_the_reel_through_a_regeneration(ready):
    """A first reel, then New voice: the second reel's line has the script, the brief
    and its own render; the first keeps only its own render; each equals the report's
    total for that reel, and together they're the month's pipeline spend."""
    from reelline.pipeline.produce import cost_line, produce
    from tests.test_produce import NAMES, FakeChecker, FakeRenderer, FakeReview

    w = ready
    first = produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=FakeRenderer(),
        review_factory=FakeReview,
        log=lambda m: None,
        checker_factory=lambda: FakeChecker(),
    )
    one = cost_line(w.conn, first.reel_id)
    script_and_brief = _cost_total(one) - 0.0123
    assert script_and_brief > 0 and "TTS $0.0123" in one
    db.update_pick(w.conn, w.pick_id, status="scripted")
    second = produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=FakeRenderer(),
        review_factory=FakeReview,
        log=lambda m: None,
        checker_factory=lambda: FakeChecker(),
    )
    one, two = cost_line(w.conn, first.reel_id), cost_line(w.conn, second.reel_id)
    assert one == ("Cost so far: $0.0123 (LLM $0.0000 + images $0.0000 + QA $0.0000 + TTS $0.0123)")
    assert _cost_total(two) == pytest.approx(script_and_brief + 0.0123, abs=1e-4)

    month = costs.month_of(datetime.fromisoformat(w.conn.execute("SELECT MAX(called_at) FROM llm_calls").fetchone()[0]))
    r = costs.report(w.conn, month)
    top = {t["reel_id"]: t["total"] for t in r.top}
    assert top[first.reel_id] == pytest.approx(_cost_total(one), abs=1e-4)
    assert top[second.reel_id] == pytest.approx(_cost_total(two), abs=1e-4)
    assert sum(
        c.usd for c in costs.reel_costs(w.conn, first.reel_id) + costs.reel_costs(w.conn, second.reel_id)
    ) == pytest.approx(r.total - r.outcome_usd.get("overhead", 0.0))


def test_review_cost_line_flags_an_upper_bound_and_unpriced_models(ready):
    from reelline.pipeline.produce import cost_line, produce
    from tests.test_produce import NAMES, FakeChecker, FakeRenderer, FakeReview

    w = ready
    img = [
        {
            "purpose": "image_generate_upper_bound",
            "model": "gemini-3.1-flash-image",
            "prompt_version": 1,
            "usage": {"input": 1, "output": 1, "thinking": 0},
            "cost_usd": 0.07,
        }
    ]
    result = produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=FakeRenderer(image_calls=img),
        review_factory=FakeReview,
        log=lambda m: None,
        checker_factory=lambda: FakeChecker(),
    )
    db.record_llm_call(
        w.conn, "story", "claude-mystery", 1, 1, {"input": 1, "output": 1, "thinking": 0}, script_id=w.script_id
    )
    line = cost_line(w.conn, result.reel_id)
    assert "images $0.0700 (upper bound)" in line and line.endswith("; no price for claude-mystery")


# ---- cost rows written as each call returns -----------------------------------------------------


def test_a_render_killed_mid_way_has_already_recorded_its_spend(ready):
    """Each row is in llm_calls the moment its call returns (reelline/media/costhook.py): the
    renderer sees them there before it 'dies'; nothing is left only in memory."""
    from reelline.pipeline.produce import produce
    from tests.test_produce import NAMES, FakeReview

    w = ready
    seen = {}

    class Killed(BaseException):
        pass

    def renderer(script_path, background, mood, regen=(), **options):
        tts_row(100, 1000)  # two fresh clips, then the process dies
        tts_row(80, 900)
        seen["rows"] = w.conn.execute(
            "SELECT purpose, script_id, pick_id, reel_id FROM llm_calls WHERE purpose = 'tts'"
        ).fetchall()
        raise Killed()

    with pytest.raises(Killed):
        produce(w.conn, "2026-10-01", "13:00", NAMES, renderer=renderer, review_factory=FakeReview, log=lambda m: None)
    assert [tuple(r) for r in seen["rows"]] == [("tts", w.script_id, w.pick_id, None)] * 2
    assert w.conn.execute("SELECT COUNT(*) FROM llm_calls WHERE purpose = 'tts'").fetchone()[0] == 2  # not doubled


def test_live_rows_get_the_reel_once_and_are_never_doubled(ready):
    from reelline.pipeline.produce import produce
    from tests.test_produce import NAMES, FakeChecker, FakeRenderer, FakeReview
    from tests.test_produce import call_row as vision_row

    w = ready
    base = FakeRenderer()

    def renderer(script_path, background, mood, regen=(), **options):
        r = base(script_path, background, mood, regen, **options)
        r.tts_calls = [tts_row(100, 1000)]
        r.image_calls = [vision_row("image_vision", "gemini-3.8-flash", 2, 1000, 100, 50)]
        return r

    result = produce(
        w.conn,
        "2026-10-01",
        "13:00",
        NAMES,
        renderer=renderer,
        review_factory=FakeReview,
        log=lambda m: None,
        checker_factory=lambda: FakeChecker(),
    )
    rows = w.conn.execute(
        "SELECT purpose, reel_id, script_id FROM llm_calls WHERE purpose IN ('tts', 'image_vision')"
    ).fetchall()
    assert sorted(tuple(r) for r in rows) == [
        ("image_vision", result.reel_id, w.script_id),
        ("tts", result.reel_id, w.script_id),
    ]


def test_a_reroll_take_recorded_live_is_relabelled(ready):
    from reelline.pipeline.produce import produce
    from tests.test_produce import LOW_ROW, NAMES, FakeReview, RerollRenderer, old_take, stem_of

    w = ready
    old_take(w, stem_of(w))
    base = RerollRenderer(rows=[LOW_ROW], reroll_rows=[])

    def renderer(script_path, background, mood, regen=(), **options):
        r = base(script_path, background, mood, regen, **options)
        r.tts_calls = [tts_row(10, 100)] if regen else [tts_row(100, 1000)]
        return r

    produce(w.conn, "2026-10-01", "13:00", NAMES, renderer=renderer, review_factory=FakeReview, log=lambda m: None)
    assert [r[0] for r in w.conn.execute("SELECT purpose FROM llm_calls WHERE purpose LIKE 'tts%' ORDER BY id")] == [
        "tts",
        "tts_reroll",
    ]


def test_manual_recording_writes_as_it_goes_and_record_manual_adds_nothing(tmp_path):
    path = tmp_path / "m.db"
    logged = []
    with db.manual_recording(db_path=path):
        row = tts_row(1000, 1000, "tts_tag")
    assert row["recorded_id"]
    db.record_manual([row], db_path=path, log=logged.append)
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*), MIN(manual) FROM llm_calls").fetchone()[:] == (1, 1)
    assert "recorded as they happened" in logged[0]


def test_without_a_recorder_nothing_is_written_live():
    row = tts_row(1, 1)
    assert "recorded_id" not in row
