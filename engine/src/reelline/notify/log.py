"""The log: a Notifier that writes each message to a log function (stdout by default) or, with
notify.log.path, appends it to a file. No network; for a channel run without a chat and for tests.
Buttons are written under the text as "[label] action"; refs count up from 1.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from reelline.config import conf
from reelline.notify.base import Buttons, MessageRef, buttons_text


class LogNotifier:
    name = "log"
    text_limit = 1_000_000

    def __init__(self, write: Callable[[str], None] | None = None, path: Path | None = None):
        path = path or (Path(conf().notify.log.path) if conf().notify.log.path else None)
        self._write = write or (self._appender(path) if path else (lambda line: print(line, flush=True)))
        self._next = 0

    @staticmethod
    def _appender(path: Path) -> Callable[[str], None]:
        def write(line: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")

        return write

    def _ref(self) -> int:
        self._next += 1
        return self._next

    def send(
        self, text: str, buttons: Buttons | None = None, *, reply_to: MessageRef | None = None
    ) -> MessageRef | None:
        ref = self._ref()
        extra = buttons_text(buttons)
        head = f"[notify #{ref}" + (f" re #{reply_to}" if reply_to is not None else "") + "]"
        self._write(f"{head} {text}" + (f"\n{extra}" if extra else ""))
        return ref

    def edit(self, ref: MessageRef, text: str, buttons: Buttons | None = None) -> None:
        extra = buttons_text(buttons)
        self._write(f"[notify #{ref} edited] {text}" + (f"\n{extra}" if extra else ""))

    def edit_buttons(self, ref: MessageRef, buttons: Buttons) -> None:
        self._write(f"[notify #{ref} buttons] {buttons_text(buttons) or '(none)'}")

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
        ref = self._ref()
        where = f" {data}" if isinstance(data, Path) else f" ({len(data)} bytes)"
        self._write(f"[notify #{ref}] {kind} {filename}{where}" + (f": {caption}" if caption else ""))
        return ref
