"""The terminal as the review channel: reviews and messages printed, decisions from stdin or from
`reelline review tap <action> | say <text>` (reelline/review/inbox.py), for a channel run by hand.

Printed: "[#<ref>] <text>" and, per button, "  [label]  reelline review tap <action>"; an edit
prints the message again ("[#<ref> edited]"). A line on stdin (review.cli.stdin, read only when
it's a terminal, or from a stream handed in) is an action ("r:12:ap": a tap), a command ("/status")
or text (a new caption). There is no cursor: the inbox files are the queue.
"""

from __future__ import annotations

import re
import select
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from reelline.config import conf
from reelline.notify.base import Buttons, MessageRef
from reelline.review import inbox
from reelline.review.base import Event, Incoming, ReviewInfo, Tap, parse_command

ACTION = re.compile(r"^[a-z]+:\S+$")
DEFAULT = object()


class CliReview:
    name = "cli"
    text_limit = 1_000_000

    def __init__(
        self,
        out: Callable[[str], None] | None = None,
        stdin: TextIO | object | None = DEFAULT,
        inbox_dir: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.out = out or (lambda line: print(line, flush=True))
        if stdin is DEFAULT:  # review.cli.stdin: the process's own; None: the inbox only
            stdin = sys.stdin if conf().review.cli.stdin else None
        self.stdin = stdin
        self.inbox_dir = inbox_dir
        self.sleep = sleep
        self._next = 0
        self._lines = 0

    # -- sending --

    def _print(self, head: str, text: str, buttons: Buttons | None) -> None:
        lines = [f"{head} {text}"]
        lines += [f"  [{label}]  reelline review tap {data}" for row in buttons or [] for label, data in row]
        self.out("\n".join(lines))

    def send(
        self, text: str, buttons: Buttons | None = None, *, reply_to: MessageRef | None = None
    ) -> MessageRef | None:
        self._next += 1
        self._print(f"[#{self._next}" + (f" re #{reply_to}" if reply_to is not None else "") + "]", text, buttons)
        return self._next

    def edit(self, ref: MessageRef, text: str, buttons: Buttons | None = None) -> None:
        self._print(f"[#{ref} edited]", text, buttons)

    def edit_buttons(self, ref: MessageRef, buttons: Buttons) -> None:
        self._print(f"[#{ref} buttons]", "", buttons)

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
        if isinstance(data, bytes):
            where = (self.inbox_dir or inbox.inbox_dir()).parent / "review_files" / filename
            where.parent.mkdir(parents=True, exist_ok=True)
            where.write_bytes(data)
        else:
            where = Path(data)
        return self.send(f"📎 {kind}: {where}" + (f"\n{caption}" if caption else ""))

    def send_review(self, text: str, buttons: Buttons, info: ReviewInfo) -> MessageRef | None:
        flags = "".join(f"\n⚠️ {k}: " + "; ".join(v) for k, v in info.flags.items())
        head = (
            f"=== Review #{info.review_id} · reel #{info.reel_id} · {info.slot_date} {info.slot_time} ==="
            + (f"\nvideo: {info.video}" if info.video else "")
            + flags
        )
        return self.send(f"{head}\n{text}", buttons)

    def send_video(self, path: Path, caption: str) -> None:
        self.send(f"🎬 {path}\n{caption}")

    # -- receiving --

    def parse(self, raw: Any) -> list[Event]:
        if isinstance(raw, Event):
            return [raw]
        line = str(raw).strip()
        if ACTION.match(line):
            return [Tap(line, "cli")]
        if line.startswith("tap ") and ACTION.match(line[4:].strip()):
            return [Tap(line[4:].strip(), "cli")]
        event = parse_command(line, "cli")
        return [event] if event else []

    def _stdin_lines(self, wait: float) -> list[str]:
        if self.stdin is None:
            return []
        if hasattr(self.stdin, "isatty") and self.stdin.isatty():
            ready, _, _ = select.select([self.stdin], [], [], wait)
            return [self.stdin.readline()] if ready else []
        line = self.stdin.readline()  # a stream handed in (a test, a pipe): what's there
        return [line] if line else []

    def receive(self, cursor: str | None, timeout: int) -> list[Incoming]:
        deadline = time.monotonic() + timeout
        while True:
            got = inbox.take(self.inbox_dir)
            for line in self._stdin_lines(min(1.0, max(0.0, deadline - time.monotonic()))):
                self._lines += 1
                got.append(Incoming(f"stdin:{self._lines}", self.parse(line), None))
            if got or time.monotonic() >= deadline:
                return got
            if self.stdin is None or not (hasattr(self.stdin, "isatty") and self.stdin.isatty()):
                self.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def ack(self, tap: Tap, text: str | None = None) -> None:
        if text:
            self.out(f"  -> {text}")
