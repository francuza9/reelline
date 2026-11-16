"""What the bot's mixins share: the attributes ApproveBot sets up and the methods they call on each other,
declared once so each mixin type-checks on its own. Nothing here exists at runtime but the base class."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from reelline.review.base import Tap


class BotState:
    conn: sqlite3.Connection
    names: dict[str, str]
    now: Callable[[], datetime]
    alive: Callable[[int | None, int], bool]
    log: Callable[[str], None]
    kill: Callable[[int], None]
    children: dict[int, Any]
    picker: Any
    stopping: bool
    publish_clients: Callable[[int], Any]
    _spawn: Callable[[int], Any]

    if TYPE_CHECKING:

        @property
        def tg(self) -> Any: ...

        def say(self, text: str, markup: list | None = None, reply_to: Any = None) -> Any: ...

        def answer(self, query: Tap, text: str) -> None: ...

        def refresh(self, review_id: int, **kw: Any) -> sqlite3.Row: ...

        def pick(self, pick_id: int) -> sqlite3.Row: ...

        def slot_dt(self, pick: sqlite3.Row) -> datetime: ...

        def dispatch(self) -> None: ...

        def publish_tick(self, retried: int | None = None) -> None: ...

        def on_unschedule(self, query: Tap, r: sqlite3.Row, by: str) -> None: ...

        def on_event(self, event: Any) -> None: ...
