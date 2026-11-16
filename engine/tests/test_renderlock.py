"""One reel render at a time (reelline/media/renderlock.py), and how a job's step limit sees the wait."""

import json
import os
import threading
import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta

import pytest
from reelline.media import renderlock
from reelline.media.renderlock import Observer, RenderLockTimeout, render_lock
from reelline.pipeline import approve_bot as ab
from reelline.pipeline import db
from tests.fx_review import FakeTg


def test_two_concurrent_renders_serialize(tmp_path):
    lock = tmp_path / "render.lock"
    inside, overlap, order = [], [], []

    def render(n):
        with render_lock(path=lock, poll_s=0.01):
            inside.append(n)
            overlap.append(len(inside))
            order.append(f"start {n}")
            time.sleep(0.15)
            order.append(f"end {n}")
            inside.remove(n)

    threads = [threading.Thread(target=render, args=(n,)) for n in (1, 2)]
    threads[0].start()
    time.sleep(0.05)
    threads[1].start()
    for t in threads:
        t.join()
    assert overlap == [1, 1]
    assert order == ["start 1", "end 1", "start 2", "end 2"]
    assert not (tmp_path / "render.lock.json").exists()


def test_a_dead_holders_lock_is_reclaimed(tmp_path):
    lock = tmp_path / "render.lock"
    lock.touch()
    (tmp_path / "render.lock.json").write_text(json.dumps({"pid": 2**22 + 12345, "job": 7}))  # no such pid
    assert renderlock.holder(lock) is None
    with render_lock(path=lock, poll_s=0.01, max_wait_s=1) as waited:
        assert waited < 1
        assert renderlock.holder(lock)["pid"] == os.getpid()


def test_a_waiter_names_the_holder_and_gives_up_after_the_limit(tmp_path):
    lock = tmp_path / "render.lock"
    now = [0.0]
    seen = []
    obs = Observer(lambda who, waited: seen.append(who), lambda waited: None)

    def sleep(s):
        now[0] += s

    with ExitStack() as stack:
        with renderlock.observing(obs, job_id=3):
            stack.enter_context(render_lock(path=lock))
        with (
            pytest.raises(
                RenderLockTimeout,
                match=r"gave up waiting for the render lock after 90 min "
                r"\(held by job #3\)",
            ),
            renderlock.observing(obs, job_id=4),
            render_lock(path=lock, poll_s=60, max_wait_s=90 * 60, sleep=sleep, clock=lambda: now[0]),
        ):
            pass
    assert len(seen) == 90 and set(seen) == {"job #3"}


def test_a_waiting_job_heartbeats_and_the_wait_is_not_the_steps_time(ready, monkeypatch, tmp_path):
    lock = tmp_path / "render.lock"
    base = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    tick = [0]

    def fake_now_iso(at=None):
        if at is not None:
            return at.isoformat()
        tick[0] += 1
        return (base + timedelta(minutes=tick[0])).isoformat()

    monkeypatch.setattr(db, "now_iso", fake_now_iso)
    job_id = ab.add_job(ready.conn, "qa_review", db.slot_pick(ready.conn, "2026-10-01", "13:00"), detail={"reel_id": 5})
    logs, step_ats, clock = [], [], [0.0]
    stack = ExitStack()
    stack.enter_context(render_lock(path=lock))  # another render holds it

    def step_at():
        return ab.get_job(ready.conn, job_id)["step_at"]

    class Steps:
        def qa_review(self, reel_id):
            step_ats.append(step_at())
            polls = [0]

            def sleep(s):
                clock[0] += s
                polls[0] += 1
                step_ats.append(step_at())
                if polls[0] == 100:  # 100 min waited, the limit is 45
                    stack.close()

            with render_lock(path=lock, poll_s=60, max_wait_s=200 * 60, sleep=sleep, clock=lambda: clock[0]):
                step_ats.append(step_at())
                return "rendered"

    assert ab.run_job(ready.conn, job_id, FakeTg(), Steps(), log=logs.append) == "done"
    assert any("waiting for the render lock (held by pid" in m for m in logs)
    assert any("got the render lock after 6000 s" in m for m in logs)
    # Each poll moved step_at (the heartbeat), and the acquisition moved it again: at no point
    # did the step look older than a poll, so the reaper never fires on a waiting job.
    parsed = [datetime.fromisoformat(s) for s in step_ats]
    assert len(parsed) == 102 and parsed == sorted(parsed) and len(set(parsed)) == len(parsed)
    for t in parsed:
        row = {"status": "running", "step": "qa", "step_at": t.isoformat(), "started_at": base.isoformat()}
        assert ab.overdue(row, t + timedelta(minutes=2)) is None
