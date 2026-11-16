"""review.channel -> the ReviewChannel: get_review_channel().

`given` mirrors reelline/providers/registry.py: a ReviewChannel is used as it is; anything else (a
TelegramBot, a test's fake of one, an httpx client for the webhook) becomes the connection of the
configured adapter; None: the adapter on a new connection (Telegram: the token from the env file).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from reelline.config import conf
from reelline.review.base import ReviewChannel


def _adapter(kind: str) -> Callable[[Any], ReviewChannel]:
    if kind == "telegram":
        from reelline.review.telegram import TelegramReview

        return lambda given: TelegramReview(given)
    if kind == "cli":
        from reelline.review.cli import CliReview

        return lambda given: CliReview()
    if kind == "webhook":
        from reelline.review.webhook import WebhookReview

        return lambda given: WebhookReview(http=given)
    if kind == "auto":
        from reelline.review.auto import AutoReview

        return lambda given: AutoReview()
    raise ValueError(f"no review channel {kind!r} (review.channel: telegram, cli, webhook, auto)")


def get_review_channel(given: Any = None, kind: str | None = None) -> ReviewChannel:
    if isinstance(given, ReviewChannel):
        return given
    return _adapter(kind or conf().review.channel)(given)
