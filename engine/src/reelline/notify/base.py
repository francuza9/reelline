"""The Notifier protocol: what the pipeline says to its operator, channel-neutral.

A message is text, optionally with buttons: rows of (label, action data). The action data
is the pipeline's own string ("j:<job id>:rt", "p:<publication id>:rt", "c:m:this", ...);
an adapter renders the buttons its own way (Telegram: inline buttons whose callback_data is
the action data; Slack, the log: text) and the review channel (reelline/review/) brings a tap
on one back as a Tap with that data. A sent message has a ref (the channel's message id) for
an edit or a reply; channels that can't edit or thread take both as plain sends.

Alerts, job failures with a Retry button, stats lines and cost reports all go through send()
(and send_file() for the CSV, the audio candidates and the review videos).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from reelline.core.errors import NotifyError as NotifyError  # re-exported: it moved to reelline.core.errors

Button = tuple[str, str]  # (label, action data)
Buttons = list[list[Button]]  # rows
MessageRef = int | str  # a sent message, for edit() and reply_to
FILE_KINDS = ("document", "audio", "video", "photo")


def buttons(rows: Iterable[Iterable[Button]]) -> Buttons:
    """A button set: rows of (label, action data), as lists."""
    return [[(str(label), str(data)) for label, data in row] for row in rows]


@runtime_checkable
class Notifier(Protocol):
    name: str

    @property
    def text_limit(self) -> int:
        """The longest message text the channel takes."""
        ...

    def send(
        self, text: str, buttons: Buttons | None = None, *, reply_to: MessageRef | None = None
    ) -> MessageRef | None: ...

    def edit(self, ref: MessageRef, text: str, buttons: Buttons | None = None) -> None:
        """The message's text replaced; its buttons replaced by these (none: removed)."""

    def edit_buttons(self, ref: MessageRef, buttons: Buttons) -> None: ...

    def send_file(
        self,
        filename: str,
        data: bytes | Path,
        *,
        kind: str = "document",
        caption: str | None = None,
        mime: str = "text/csv",
        meta: dict[str, Any] | None = None,
    ) -> MessageRef | None:
        """A file (kind: document, audio, video, photo); meta: the channel's extras (a video's width,
        height, duration; an audio's title, performer), ignored where they mean nothing."""


def split_text(text: str, limit: int) -> list[str]:
    """Pieces of at most `limit` characters, split at line ends where possible."""
    out, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                out.append(current)
                current = ""
            out.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            out.append(current)
            current = line
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def send_chunks(notifier: Notifier, text: str, buttons: Buttons | None = None) -> MessageRef | None:
    """A long text as several messages (split at line ends); the buttons on the last one."""
    chunks = split_text(text, notifier.text_limit)
    ref = None
    for i, chunk in enumerate(chunks):
        ref = notifier.send(chunk, buttons if i == len(chunks) - 1 else None)
    return ref


def file_bytes(data: bytes | Path) -> bytes:
    return data if isinstance(data, bytes) else Path(data).read_bytes()


def buttons_text(rows: Buttons | None) -> str:
    """Buttons as text, for channels without them: "[label] action" per button."""
    return "\n".join(f"[{label}] {data}" for row in rows or [] for label, data in row)
