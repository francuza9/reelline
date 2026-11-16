"""One reel render at a time on the machine.

Two chains' renders at once (Remotion head + gag + four platform versions each) starved
the 4 vCPUs and the reaper killed both jobs at the produce step's limit. The rendering
part of a reel (scripts/render_reel.py) takes this lock: an flock on out/render.lock,
which the kernel drops with its holder, so a crashed holder never blocks anyone. Next to
it out/render.lock.json says who holds it ({"pid", "job"}), for the waiting job's log; a
holder file whose pid is gone is stale and is simply replaced by the next holder.

A job waiting for the lock calls the observer's waiting() on every poll (the worker's
heartbeat: it moves jobs.step_at, so the reaper sees a live step) and acquired() once it
holds the lock (it moves step_at again: the wait doesn't count toward the step's limit).
Waiting longer than MAX_WAIT_S raises RenderLockTimeout."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from reelline.core.errors import ReellineError
from reelline.paths import REPO_ROOT

LOCK_FILE = REPO_ROOT / "out" / "render.lock"
POLL_S = 5.0
MAX_WAIT_S = 90 * 60


class RenderLockTimeout(ReellineError, RuntimeError):
    pass


@dataclass
class Observer:
    waiting: Callable[[str, float], None]  # (who holds it, seconds waited so far)
    acquired: Callable[[float], None]  # (seconds waited)


_OBSERVER: ContextVar[Observer | None] = ContextVar("render_lock_observer", default=None)
_JOB: ContextVar[int | None] = ContextVar("render_lock_job", default=None)


@contextlib.contextmanager
def observing(observer: Observer, job_id: int | None = None) -> Iterator[None]:
    """Inside, a render waiting for the lock reports to `observer`; job_id names this
    process in the holder file."""
    tokens = (_OBSERVER.set(observer), _JOB.set(job_id))
    try:
        yield
    finally:
        _OBSERVER.reset(tokens[0])
        _JOB.reset(tokens[1])


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def holder(path: Path | None = None) -> dict | None:
    """Who the holder file names, if that process is still there."""
    try:
        info = json.loads(((path or LOCK_FILE).with_suffix(".lock.json")).read_text(encoding="utf-8"))
        return info if _alive(int(info["pid"])) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def describe(info: dict | None) -> str:
    if not info:
        return "another render"
    return f"job #{info['job']}" if info.get("job") is not None else f"pid {info['pid']}"


@contextlib.contextmanager
def render_lock(
    *,
    path: Path | None = None,
    poll_s: float | None = None,
    max_wait_s: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[float]:
    """Hold the render lock for the block; yields the seconds spent waiting."""
    path = path or LOCK_FILE
    poll_s = POLL_S if poll_s is None else poll_s
    max_wait_s = MAX_WAIT_S if max_wait_s is None else max_wait_s
    observer = _OBSERVER.get()
    info_path = path.with_suffix(".lock.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    start = clock()
    with path.open("a+") as fd:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                waited = clock() - start
                who = describe(holder(path))
                if waited >= max_wait_s:
                    raise RenderLockTimeout(
                        f"gave up waiting for the render lock after {int(waited // 60)} min (held by {who})"
                    ) from None
                if observer:
                    observer.waiting(who, waited)
                sleep(poll_s)
        waited = clock() - start
        info_path.write_text(json.dumps({"pid": os.getpid(), "job": _JOB.get()}), encoding="utf-8")
        if observer:
            observer.acquired(waited)
        try:
            yield waited
        finally:
            info_path.unlink(missing_ok=True)
            fcntl.flock(fd, fcntl.LOCK_UN)
