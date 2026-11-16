"""Telegram: the Bot API client and the Notifier adapter (the review adapter, reelline/review/telegram.py,
builds on both). The only Telegram code with reelline/review/telegram.py (tests/test_channel_boundary.py).

TelegramBot: a small Bot API client: messages with inline buttons, edits, callback answers, files
and getUpdates long polling. Credentials: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env
(reelline/config/credentials.py); the token never appears in errors (redacted). Settings:
review.telegram (the API base, timeouts, the message limit; files: the upload timeouts).

TelegramNotifier: Notifier over a TelegramBot (or anything with its surface, a test's fake): buttons
become inline_keyboard markup whose callback_data is the action data, so messages and buttons are
the ones the bot always sent.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

from reelline.config import conf
from reelline.config.credentials import TelegramSettings
from reelline.notify.base import Button, Buttons, MessageRef, NotifyError

# The Bot API's method and form field per file kind (core.telegram.org/bots/api, "Sending files").
FILE_METHODS = {
    "document": ("sendDocument", "document"),
    "audio": ("sendAudio", "audio"),
    "video": ("sendVideo", "video"),
    "photo": ("sendPhoto", "photo"),
}


class TelegramError(NotifyError):
    """A Bot API call that failed (network, HTTP, or ok: false)."""


def keyboard(rows: Iterable[Iterable[Button]] | None) -> dict:
    """reply_markup for inline buttons; callback_data is at most 64 bytes."""
    markup = []
    for row in rows or []:
        line = []
        for label, data in row:
            if len(data.encode()) > 64:
                raise TelegramError(f"callback_data over 64 bytes: {data!r}")
            line.append({"text": label, "callback_data": data})
        markup.append(line)
    return {"inline_keyboard": markup}


class TelegramBot:
    def __init__(self, settings: TelegramSettings, http: httpx.Client | None = None):
        self._settings = settings
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(
                connect=conf().review.telegram.connect_timeout_s,
                read=conf().review.telegram.read_timeout_s,
                write=conf().review.telegram.connect_timeout_s,
                pool=conf().review.telegram.connect_timeout_s,
            )
        )

    @property
    def chat_id(self) -> str:
        return str(self._settings.chat_id)

    def _redact(self, text: str) -> str:
        return text.replace(self._settings.bot_token, "<token>")

    def call(
        self, method: str, payload: dict, files: dict | None = None, timeout: httpx.Timeout | None = None
    ) -> object:
        """One Bot API call: JSON, or multipart with `files` (payload values sent as form fields)."""
        url = f"{conf().review.telegram.api_base}/bot{self._settings.bot_token}/{method}"
        extra = {"timeout": timeout} if timeout is not None else {}
        try:
            if files:
                form = {k: v if isinstance(v, str) else json.dumps(v) for k, v in payload.items()}
                response = self._http.post(url, data=form, files=files, **extra)
            else:
                response = self._http.post(url, json=payload, **extra)
        except httpx.HTTPError as exc:
            raise TelegramError(f"Telegram {method}: {type(exc).__name__}: {self._redact(str(exc))}") from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramError(f"Telegram {method}: HTTP {response.status_code}, non-JSON reply") from None
        if not body.get("ok"):
            raise TelegramError(
                f"Telegram {method}: HTTP {response.status_code}: {self._redact(str(body.get('description')))}"
            )
        return body["result"]

    # ---- sending ------------------------------------------------------------------

    def send_message(self, text: str, markup: dict | None = None, reply_to: int | None = None) -> dict:
        if len(text) > conf().review.telegram.text_max_chars:
            raise TelegramError(f"Message is {len(text)} characters, over {conf().review.telegram.text_max_chars}")
        payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if markup is not None:
            payload["reply_markup"] = markup
        if reply_to is not None:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return self.call("sendMessage", payload)

    def send_document(self, filename: str, data: bytes, caption: str | None = None, mime: str = "text/csv") -> dict:
        payload = {"chat_id": self.chat_id}
        if caption:
            payload["caption"] = caption
        return self.call("sendDocument", payload, files={"document": (filename, data, mime)})

    def send_file(
        self,
        kind: str,
        filename: str,
        data,
        caption: str | None = None,
        mime: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        """An audio, video or photo upload (the long upload timeouts: up to 50 MB on a slow uplink)."""
        method, field = FILE_METHODS[kind]
        payload = {"chat_id": self.chat_id, **{k: str(v) for k, v in (meta or {}).items()}}
        if caption:
            payload["caption"] = caption
        t = conf().review.telegram
        timeout = httpx.Timeout(
            connect=t.connect_timeout_s,
            read=t.upload_read_timeout_s,
            write=t.upload_write_timeout_s,
            pool=t.connect_timeout_s,
        )
        return self.call(method, payload, files={field: (filename, data, mime)}, timeout=timeout)

    def edit_message(self, message_id: int, text: str, markup: dict | None = None) -> None:
        payload = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": markup or {"inline_keyboard": []},
        }
        self.call("editMessageText", payload)

    def edit_markup(self, message_id: int, markup: dict) -> None:
        self.call("editMessageReplyMarkup", {"chat_id": self.chat_id, "message_id": message_id, "reply_markup": markup})

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self.call("answerCallbackQuery", payload)

    # ---- receiving ----------------------------------------------------------------

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["callback_query", "message"]}
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload)

    def from_our_chat(self, update: dict) -> bool:
        if "callback_query" in update:
            message = update["callback_query"].get("message") or {}
        else:
            message = update.get("message") or {}
        return str((message.get("chat") or {}).get("id")) == self.chat_id


def connect() -> TelegramBot:
    """A client for the review chat from the env file (read each time: a long-running bot picks up a new token)."""
    from reelline.config.credentials import load_telegram

    return TelegramBot(load_telegram())


class TelegramNotifier:
    """Notifier over a TelegramBot (the review chat)."""

    name = "telegram"

    def __init__(self, bot: Any = None):
        self.bot = bot if bot is not None else connect()

    @property
    def text_limit(self) -> int:
        return conf().review.telegram.text_max_chars

    def send(
        self, text: str, buttons: Buttons | None = None, *, reply_to: MessageRef | None = None
    ) -> MessageRef | None:
        message = self.bot.send_message(text, keyboard(buttons) if buttons is not None else None, reply_to=reply_to)
        return (message or {}).get("message_id")

    def edit(self, ref: MessageRef, text: str, buttons: Buttons | None = None) -> None:
        self.bot.edit_message(ref, text, keyboard(buttons))

    def edit_buttons(self, ref: MessageRef, buttons: Buttons) -> None:
        self.bot.edit_markup(ref, keyboard(buttons))

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
        if kind == "document":
            body = data if isinstance(data, bytes) else Path(data).read_bytes()
            message = self.bot.send_document(filename, body, caption=caption, mime=mime)
        elif isinstance(data, bytes):
            message = self.bot.send_file(kind, filename, data, caption=caption, mime=mime, meta=meta)
        else:
            with Path(data).open("rb") as fh:
                message = self.bot.send_file(kind, filename, fh, caption=caption, mime=mime, meta=meta)
        return (message or {}).get("message_id")
