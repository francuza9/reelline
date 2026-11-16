"""A TelegramBot that records its Bot API calls instead of making them (no network), and the
Notifier over it: what the scripts' and steps' sends look like on the wire."""

from pathlib import Path

from reelline.config.credentials import TelegramSettings
from reelline.notify.telegram import TelegramBot, TelegramNotifier


class CallRecorder(TelegramBot):
    def __init__(self):
        super().__init__(TelegramSettings(bot_token="t0ken", chat_id="4242", env_file=Path(".env")), http=object())
        self.calls = []

    def call(self, method, payload, files=None, timeout=None):
        data = {k: v for k, v in payload.items() if k != "chat_id"}
        self.calls.append((method, data, {k: v[0] for k, v in (files or {}).items()}))
        return {"message_id": len(self.calls)}


def recording_notifier() -> TelegramNotifier:
    """notifier.bot.calls: (method, payload without chat_id, {field: filename})."""
    return TelegramNotifier(CallRecorder())
