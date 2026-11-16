"""FakeReviewChannel: a ReviewChannel with no channel behind it, for channel-neutral tests.

Everything sent is recorded (FakeNotifier: .sent with neutral buttons, .files); send_review() also
records the ReviewInfo in .reviews and send_video() the path in .videos; .incoming is what
receive() hands out next (events, or Incoming updates); ack() appends (data, text) to .acks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reelline.notify.base import Buttons, MessageRef
from reelline.notify.fakes import FakeNotifier
from reelline.review.base import Event, Incoming, ReviewInfo, Tap


class FakeReviewChannel(FakeNotifier):
    name = "fake"

    def __init__(self, text_limit: int = 4096):
        super().__init__(text_limit)
        self.reviews: list[tuple[MessageRef, ReviewInfo]] = []
        self.videos: list[tuple[Path, str]] = []
        self.incoming: list[Incoming | Event] = []
        self.acks: list[tuple[str, str | None]] = []

    def send_review(self, text: str, buttons: Buttons, info: ReviewInfo) -> MessageRef | None:
        ref = self.send(text, buttons)
        self.reviews.append((ref, info))
        return ref

    def send_video(self, path: Path, caption: str) -> None:
        self.videos.append((path, caption))

    def parse(self, raw: Any) -> list[Event]:
        return [raw] if isinstance(raw, Event) else []

    def receive(self, cursor: str | None, timeout: int) -> list[Incoming]:
        out = []
        for i, item in enumerate(self.incoming):
            out.append(item if isinstance(item, Incoming) else Incoming(f"fake:{i}", [item], None))
        self.incoming = []
        return out

    def ack(self, tap: Tap, text: str | None = None) -> None:
        self.acks.append((tap.data, text))

    # helpers
    def last_ack(self) -> str | None:
        return self.acks[-1][1] if self.acks else None

    @staticmethod
    def labels(buttons: Buttons | None) -> list[str]:
        return [label for row in buttons or [] for label, _ in row]
