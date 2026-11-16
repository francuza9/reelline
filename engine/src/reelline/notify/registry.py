"""notify.channel -> the Notifier: get_notifier().

`given` mirrors reelline/providers/registry.py: a Notifier is used as it is; anything else (a
TelegramBot, a test's fake of one, an httpx client for Slack, a log function) becomes the
connection of the configured adapter; None: the adapter on a new connection (from the env file).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from reelline.config import conf
from reelline.notify.base import Notifier


def _adapter(kind: str) -> Callable[[Any], Notifier]:
    if kind == "telegram":
        from reelline.notify.telegram import TelegramNotifier

        return lambda given: TelegramNotifier(given)
    if kind == "slack":
        from reelline.notify.slack import SlackNotifier

        return lambda given: SlackNotifier(http=given)
    if kind == "log":
        from reelline.notify.log import LogNotifier

        return lambda given: LogNotifier(write=given)
    raise ValueError(f"no notifier {kind!r} (notify.channel: telegram, slack, log)")


def get_notifier(given: Any = None, kind: str | None = None) -> Notifier:
    if isinstance(given, Notifier):
        return given
    return _adapter(kind or conf().notify.channel)(given)
