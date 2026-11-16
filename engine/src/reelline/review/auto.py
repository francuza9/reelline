"""Auto-approval as the review channel, for unattended (demo) channels: every review is approved
review.auto.delay_s after its buttons went out, with no one watching.

The channel presses the first button it is shown whose review action is in review.auto.press
(approve; then Yes when QA failed, Post now when the slot has passed), once per message and
action, as a Tap from "auto" that the bot handles like any other (its state machine, its checks).
Menus aren't answered: they time out to their #1. Everything else is printed (or given to `out`).
Pending presses are in memory: a bot restarted inside the delay leaves those reviews to expire
(or to `reelline review tap`, whose inbox this channel reads too).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reelline.config import conf
from reelline.notify.base import Buttons, MessageRef
from reelline.review import inbox
from reelline.review.base import Event, Incoming, ReviewInfo, Tap


class AutoReview:
    name = "auto"
    text_limit = 1_000_000

    def __init__(
        self,
        out: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        delay_s: float | None = None,
        inbox_dir: Path | None = None,
    ):
        self.out = out or (lambda line: print(line, flush=True))
        self.clock, self.sleep, self.inbox_dir = clock, sleep, inbox_dir
        self.delay_s = conf().review.auto.delay_s if delay_s is None else delay_s
        self.due: list[tuple[float, Tap]] = []
        self._pressed: set[tuple[MessageRef, str]] = set()
        self._next = 0

    def _press(self, ref: MessageRef, buttons: Buttons | None) -> None:
        from reelline.review.machine import parse_callback

        offered = {}
        for row in buttons or []:
            for _, data in row:
                parsed = parse_callback(data)
                if parsed is not None and parsed[0] == "r":
                    offered.setdefault(parsed[2], data)
        for action in conf().review.auto.press:
            if action in offered:
                if (ref, action) not in self._pressed:
                    self._pressed.add((ref, action))
                    self.due.append((self.clock() + self.delay_s, Tap(offered[action], "auto", ref)))
                return

    def send(
        self, text: str, buttons: Buttons | None = None, *, reply_to: MessageRef | None = None
    ) -> MessageRef | None:
        self._next += 1
        self.out(f"[#{self._next}] {text}")
        self._press(self._next, buttons)
        return self._next

    def edit(self, ref: MessageRef, text: str, buttons: Buttons | None = None) -> None:
        self.out(f"[#{ref} edited] {text}")
        self._press(ref, buttons)

    def edit_buttons(self, ref: MessageRef, buttons: Buttons) -> None:
        self._press(ref, buttons)

    def send_file(
        self,
        filename: str,
        data: bytes | Path,
        *,
        kind: str = "document",
        caption: str | None = None,
        mime: str = "text/csv",
        meta: dict | None = None,
    ) -> MessageRef | None:
        return self.send(f"📎 {kind} {filename}" + (f": {caption}" if caption else ""))

    def send_review(self, text: str, buttons: Buttons, info: ReviewInfo) -> MessageRef | None:
        return self.send(text, buttons)

    def send_video(self, path: Path, caption: str) -> None:
        self.send(f"🎬 {path}\n{caption}")

    def parse(self, raw: Any) -> list[Event]:
        return [raw] if isinstance(raw, Event) else []

    def receive(self, cursor: str | None, timeout: int) -> list[Incoming]:
        got = inbox.take(self.inbox_dir)
        now = self.clock()
        ready = [t for at, t in self.due if at <= now]
        if not ready and not got:
            nxt = min((at for at, _ in self.due), default=now + timeout)
            self.sleep(max(0.0, min(timeout, nxt - now)))
            now = self.clock()
            ready = [t for at, t in self.due if at <= now]
        self.due = [(at, t) for at, t in self.due if at > now]
        return got + [Incoming(f"auto:{t.ref}:{t.data}", [t], None) for t in ready]

    def ack(self, tap: Tap, text: str | None = None) -> None:
        if text:
            self.out(f"  -> {text}")
