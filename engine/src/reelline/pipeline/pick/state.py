"""The Picker's state: its collaborators, the pick snapshot and the after-pick brief."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime

from reelline.config import conf
from reelline.pipeline.brief import BriefError, build_brief
from reelline.pipeline.scoring import (
    Evaluation,
)
from reelline.providers import get_llm


class PickerState:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        source_names: dict[str, str],
        client_factory: Callable = lambda: get_llm("assessor"),
        channel_factory: Callable[[], object] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_picked: Callable[[int], None] | None = None,
        on_none: Callable[[sqlite3.Row], None] | None = None,
        log: Callable[[str], None] = lambda m: print(m, flush=True),
    ):
        self.conn = conn
        self.source_names = source_names
        self.client_factory = client_factory
        self._channel_factory = channel_factory or (lambda: None)
        self._channel = None
        self.now = now
        self.on_picked = on_picked or self.make_brief
        # After "None of these": called with the menu inside the transaction that closes it
        # (the approval bot queues the fallback pick job there, so the daily tick never
        # sees the slot with neither a menu nor a job). None: run() picks here, after it.
        self.on_none = on_none
        self.log = log
        self.briefs_dir = conf().storage.briefs_dir

    @property
    def bot(self):
        """The review channel the menus go to (review.channel; a raw client from the factory is wrapped)."""
        if self._channel is None:
            from reelline.review import get_review_channel

            self._channel = get_review_channel(self._channel_factory())
        return self._channel

    def snapshot(self, evaluations: list[Evaluation], n: int | None = None) -> list[dict]:
        return [
            {
                "story_id": e.candidate.story_id,
                "topic": e.candidate.topic,
                "effective": e.effective,
                "base": e.candidate.base_score,
                "hook": e.candidate.hook_strength,
                "summary": e.candidate.summary,
                "title": e.candidate.title,
                "source": self.source_names.get(e.candidate.source_id, e.candidate.source_id),
                "url": e.candidate.canonical_url,
                "deadline": e.candidate.deadline.isoformat() if e.candidate.deadline else None,
                "parts": e.parts,
            }
            for e in evaluations[: n or conf().steps.pick.menu_size]
        ]

    @staticmethod
    def hook_text(hook: int | None) -> str:
        return "" if hook is None else f" hook {hook}"

    def describe(self, e: Evaluation) -> str:
        c = e.candidate
        deadline = f", deadline {c.deadline.isoformat()}" if c.deadline else ""
        return (
            f"#{c.story_id} [{c.topic} {e.effective:.2f}{self.hook_text(c.hook_strength)}] {c.summary} "
            f"({self.source_names.get(c.source_id, c.source_id)}{deadline})"
        )

    def make_brief(self, pick_id: int) -> None:
        """The default after-pick step: the fact brief for the picked story."""
        pick = self.conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone()
        try:
            brief = build_brief(
                self.conn, self.client_factory(), pick["story_id"], self.source_names, pick_id, out_dir=self.briefs_dir
            )
        except BriefError as exc:
            raise BriefError(
                f"pick #{pick_id} is recorded, but its brief failed ({exc}); "
                f"retry with `python -m reelline.pipeline.pick brief {pick['story_id']}`"
            ) from None
        self.log(f"Brief: {brief.path}")
        for warning in brief.warnings:
            self.log(f"  check: {warning}")

    def run(self, slot_day: date, slot_time: str, dry_run: bool = False, menu: bool = True) -> int:
        """Pick for one slot (Picker.run); declared here for the menu methods that fall back to it."""
        raise NotImplementedError
