"""FakeNotifier: a Notifier that records every message (no network). Refs count up from 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reelline.notify.base import Buttons, MessageRef, NotifyError


@dataclass
class Sent:
    ref: MessageRef
    text: str
    buttons: Buttons | None = None
    reply_to: MessageRef | None = None
    edits: list[tuple[str, Buttons | None]] = field(default_factory=list)


class FakeNotifier:
    name = "fake"

    def __init__(self, text_limit: int = 4096, fail: bool = False):
        self.text_limit = text_limit
        self.fail = fail
        self.sent: list[Sent] = []
        self.files: list[dict] = []

    def _check(self) -> None:
        if self.fail:
            raise NotifyError("fake: down")

    def message(self, ref: MessageRef) -> Sent:
        return next(m for m in self.sent if m.ref == ref)

    def send(
        self, text: str, buttons: Buttons | None = None, *, reply_to: MessageRef | None = None
    ) -> MessageRef | None:
        self._check()
        if len(text) > self.text_limit:
            raise NotifyError(f"Message is {len(text)} characters, over {self.text_limit}")
        ref = len(self.sent) + len(self.files) + 1
        self.sent.append(Sent(ref, text, buttons, reply_to))
        return ref

    def edit(self, ref: MessageRef, text: str, buttons: Buttons | None = None) -> None:
        self._check()
        m = self.message(ref)
        m.text, m.buttons = text, buttons
        m.edits.append((text, buttons))

    def edit_buttons(self, ref: MessageRef, buttons: Buttons) -> None:
        self._check()
        self.message(ref).buttons = buttons

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
        self._check()
        self.files.append(
            {"filename": filename, "data": data, "kind": kind, "caption": caption, "mime": mime, "meta": meta}
        )
        return len(self.sent) + len(self.files)

    def texts(self) -> list[str]:
        return [m.text for m in self.sent]
