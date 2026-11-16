"""Notifications (reelline/notify/base.py): the Notifier protocol for alerts, failures with a Retry
action, stats lines and cost reports; adapters telegram.py (the review chat), slack.py (an incoming
webhook) and log.py; notify.channel picks one (registry.py). Only telegram.py and
reelline/review/telegram.py hold Telegram code (tests/test_channel_boundary.py)."""

from reelline.notify.base import Button, Buttons, MessageRef, Notifier, NotifyError, buttons, send_chunks, split_text
from reelline.notify.registry import get_notifier
from reelline.notify.text import about_reel, framed

__all__ = [
    "Button",
    "Buttons",
    "MessageRef",
    "Notifier",
    "NotifyError",
    "about_reel",
    "buttons",
    "framed",
    "get_notifier",
    "send_chunks",
    "split_text",
]
