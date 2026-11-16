"""The ReviewChannel protocol: where a reel's review goes and where the operator's decisions come from.

A ReviewChannel is a Notifier (reelline/notify/base.py: text, buttons, edits, files) that also
- sends a reel for review: send_video() (the preview), then send_review() (the status block with
  the buttons; ReviewInfo carries what a structured channel wants: the slot, the caption, the
  video, the flags such as failed fact-check claims and QA failures);
- brings back what the operator did, as channel-neutral events (receive(), parse()):
    Tap(data)        a button: its action data ("r:<review>:ap", "j:<job>:rt", "pick:<menu>:ok", ...)
    Command(name)    an operator command: status, pause, resume, costs, analytics, help, cancel
                     (the bot answers any other name with "Unknown command")
    Text(text)       free text (the new caption while one is asked for);
- acknowledges a tap (ack(): Telegram's callback answer, a line elsewhere).

Everything a decision means (the review states, idempotency, double taps, expiry, the
regeneration limit, the jobs) is decided in channel-neutral code: reelline/review/machine.py and
reelline/pipeline/approve_bot.py. Adapters (telegram.py, cli.py, webhook.py, auto.py) only render
and parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from reelline.core.errors import ReviewError as ReviewError  # re-exported: it moved to reelline.core.errors
from reelline.notify.base import Buttons, MessageRef, Notifier

COMMANDS = ("status", "pause", "resume", "costs", "analytics", "help", "start", "cancel")


@dataclass(frozen=True)
class Tap:
    data: str  # the button's action data
    user: str = "?"
    ref: MessageRef | None = None  # the message the button was on
    token: Any = None  # the adapter's handle for ack() (Telegram: the callback query id)


@dataclass(frozen=True)
class Command:
    name: str  # without the slash, lower case
    args: tuple[str, ...] = ()
    user: str = "?"


@dataclass(frozen=True)
class Text:
    text: str
    user: str = "?"


Event = Tap | Command | Text


@dataclass(frozen=True)
class Incoming:
    """One update from the channel: its events (none for an update the channel ignores, e.g. another
    chat's: `ignored` says why), and the cursor to receive from next."""

    id: str
    events: list[Event]
    cursor: str | None
    ignored: str | None = None


@dataclass(frozen=True)
class ReviewInfo:
    review_id: int
    reel_id: int
    slot_date: str
    slot_time: str
    caption: str | None = None
    video: Path | None = None
    flags: dict[str, list[str]] = field(default_factory=dict)  # factcheck, qa: the failures


@runtime_checkable
class ReviewChannel(Notifier, Protocol):
    def send_review(self, text: str, buttons: Buttons, info: ReviewInfo) -> MessageRef | None:
        """The review's status block with its buttons (its last message, when split)."""

    def send_video(self, path: Path, caption: str) -> None:
        """The reel's preview (and its cover, where the channel shows one)."""

    def receive(self, cursor: str | None, timeout: int) -> list[Incoming]: ...

    def parse(self, raw: Any) -> list[Event]:
        """One raw update of the channel as events (an Event is returned as it is)."""

    def ack(self, tap: Tap, text: str | None = None) -> None: ...


def parse_command(text: str, user: str = "?") -> Event | None:
    """A chat line as an event: "/name args" a Command ("/status@bot" too), other text a Text, blank None."""
    text = text.strip()
    if not text:
        return None
    if text.startswith("/"):
        words = text.split()
        return Command(words[0].split("@")[0].lower()[1:], tuple(words[1:]), user)
    return Text(text, user)
