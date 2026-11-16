"""The one cost module: prices (config models.prices), a call's cost, and where its llm_calls row goes.

price(model) is the model's row of models.prices (USD per 1M tokens: input, output, and
output_image for an image model), None when it has none. cost_usd(model, usage) prices one call:
input at input; image output tokens at output_image; the rest of the output and the thinking at
output (thinking is billed as output). None for a model with no price (the call is recorded
without a cost, with a warning by the caller).

CostLedger is where a call's rows go:
- CostLedger(conn, script_id=, pick_id=): straight into llm_calls (reelline/pipeline/db.py
  record_llm_call()), every try: a failed one with its error and try number, the answer with the
  try that answered (the script passes, caption, end card, assess, brief, fact-check);
- CostLedger(rows=list): a cost row dict per call (purpose, model, prompt_version, usage, cost_usd,
  at) appended to the list and handed to emit() (the render's image and TTS calls, QA vision;
  reelline/pipeline/db.py record_calls() writes them with their reel);
- CostLedger(): nothing recorded (a dry run, a test).

emit()/recording(): a recorder installed with recording() (reelline/pipeline/produce.py, `qa run`,
the dev CLIs) writes each fresh cost row to llm_calls at once and marks it ("recorded_id"), so a
process killed mid-render loses no spend; recording the row later with its reel only updates it.
Without a recorder emit() does nothing and the row is recorded from memory when the run ends. One
recorder per process (renders don't use threads); a recorder that fails is reported on stderr and
the row stays in memory for the later record.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from reelline.config import conf
from reelline.providers.base import LLMUsage


def price(model: str) -> dict[str, float] | None:
    return conf().models.prices.get(model)


def cost_usd(model: str, usage: LLMUsage) -> float | None:
    """USD for one call (module docstring); None when the model has no price."""
    p = price(model)
    if p is None:
        return None
    image = usage.image or 0
    text = (usage.output or 0) + (usage.thinking or 0) - image
    if image:
        return ((usage.input or 0) * p["input"] + image * p["output_image"] + text * p["output"]) / 1_000_000
    return ((usage.input or 0) * p["input"] + text * p["output"]) / 1_000_000


def utc_now_iso() -> str:
    """Now as reelline/pipeline/db.py stores timestamps ("2026-09-30T21:05:00+00:00")."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def call_row(purpose: str, model: str, prompt_version: int, usage: LLMUsage, cost: float | None) -> dict:
    """One paid call's cost row, for reelline/pipeline/db.py record_calls(), stamped now, through emit()."""
    return emit(
        {
            "purpose": purpose,
            "model": model,
            "prompt_version": prompt_version,
            "usage": {"input": usage.input or 0, "output": usage.output or 0, "thinking": usage.thinking or 0},
            "cost_usd": cost,
            "at": utc_now_iso(),
        }
    )


class CostLedger:
    """Where one step's calls are recorded (module docstring)."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        script_id: int | None = None,
        pick_id: int | None = None,
        rows: list[dict] | None = None,
    ):
        self.conn, self.script_id, self.pick_id, self.rows = conn, script_id, pick_id, rows

    def answered(
        self,
        purpose: str,
        model: str,
        prompt_version: int,
        usage: LLMUsage,
        *,
        served: str | None = None,
        cost: float | None = None,
        attempt: int | None = None,
        items: int = 1,
    ) -> tuple[int | None, dict | None]:
        """(the llm_calls id, the cost row) of an answered call; None for what isn't kept."""
        if self.conn is not None:
            from reelline.pipeline import db

            return db.record_llm_call(
                self.conn,
                purpose,
                model,
                prompt_version,
                items,
                usage.as_dict(),
                script_id=self.script_id,
                cost_usd=cost,
                served_model=served,
                pick_id=self.pick_id,
                attempt=attempt,
            ), None
        if self.rows is not None:
            row = call_row(purpose, model, prompt_version, usage, cost)
            self.rows.append(row)
            return None, row
        return None, None

    def failed(
        self,
        purpose: str,
        model: str,
        prompt_version: int,
        error: str,
        attempt: int,
        *,
        usage: LLMUsage | None = None,
        served: str | None = None,
        cost: float | None = None,
        items: int = 1,
    ) -> None:
        """A try whose connection dropped (only into llm_calls: a render's calls make one try)."""
        if self.conn is None:
            return
        from reelline.pipeline import db

        db.record_llm_call(
            self.conn,
            purpose,
            model,
            prompt_version,
            items,
            usage.as_dict() if usage else {},
            script_id=self.script_id,
            cost_usd=cost,
            served_model=served,
            pick_id=self.pick_id,
            error=error,
            attempt=attempt,
        )


NO_LEDGER = CostLedger()


# ---- live recording ---------------------------------------------------------------------------

_recorder: Callable[[dict], None] | None = None


@contextmanager
def recording(recorder: Callable[[dict], None]) -> Iterator[None]:
    global _recorder
    previous, _recorder = _recorder, recorder
    try:
        yield
    finally:
        _recorder = previous


def emit(row: dict) -> dict:
    """Hand a fresh cost row to the recorder, if one is installed; returns the row."""
    if _recorder is not None and "recorded_id" not in row:
        try:
            _recorder(row)
        except Exception as exc:  # never lose the call over its bookkeeping
            print(
                f"WARNING: cost row not written as it happened ({type(exc).__name__}: {exc}); "
                "it will be recorded at the end of the run",
                file=sys.stderr,
                flush=True,
            )
    return row
