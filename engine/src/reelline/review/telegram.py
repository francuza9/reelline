"""Telegram as the review channel: the review chat the approval bot has always used.

TelegramReview is the Notifier adapter (reelline/notify/telegram.py) plus the review side:
- send_review(): the status block as a message with inline buttons (callback_data = the action
  data, so "r:<review>:<code>", "j:<job>:rt", "pick:..." are byte for byte what was sent before);
- send_video(): the reel and its cover through scripts/send_to_phone.py (the preview encode for a
  reel over the 50 MB upload limit, the 1024-character caption split);
- receive(): one getUpdates long poll from the cursor (the next update_id); updates from any chat
  but TELEGRAM_CHAT_ID are ignored (logged by the bot);
- parse(): a callback query -> Tap (its id the ack token), a text message -> Command ("/status",
  "/costs 2026-10", "/pause@the_bot") or Text; ack(): answerCallbackQuery.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from reelline.notify.base import Buttons, MessageRef
from reelline.notify.telegram import TelegramError, TelegramNotifier
from reelline.review.base import Event, Incoming, ReviewError, ReviewInfo, Tap, parse_command


def who(query_or_message: dict) -> str:
    user = query_or_message.get("from") or {}
    return str(user.get("username") or user.get("first_name") or user.get("id") or "?")


def _chat(update: dict) -> dict | None:
    return ((update.get("callback_query") or {}).get("message") or update.get("message") or {}).get("chat")


class TelegramReview(TelegramNotifier):
    name = "telegram"

    def send_review(self, text: str, buttons: Buttons, info: ReviewInfo) -> MessageRef | None:
        return self.send(text, buttons)

    def send_video(self, path: Path, caption: str) -> None:
        from reelline.paths import SCRIPTS_DIR

        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        from send_to_phone import SendError, send_reel

        try:
            send_reel(path, caption, check_these=False, notifier=self)
        except SendError as exc:
            raise ReviewError(str(exc)) from None

    def receive(self, cursor: str | None, timeout: int) -> list[Incoming]:
        out = []
        for update in self.bot.get_updates(int(cursor) if cursor else None, timeout):
            uid, nxt = update["update_id"], str(update["update_id"] + 1)
            if self.bot.from_our_chat(update):
                out.append(Incoming(str(uid), self.parse(update), nxt))
            else:
                out.append(Incoming(str(uid), [], nxt, ignored=f"chat {(_chat(update) or {}).get('id')}"))
        return out

    def parse(self, raw: Any) -> list[Event]:
        if isinstance(raw, Event):
            return [raw]
        if "callback_query" in raw:
            query = raw["callback_query"]
            return [
                Tap(
                    str(query.get("data", "")),
                    who(query),
                    (query.get("message") or {}).get("message_id"),
                    query.get("id"),
                )
            ]
        message = raw.get("message") or {}
        event = parse_command(message.get("text") or "", who(message))
        return [event] if event is not None else []

    def ack(self, tap: Tap, text: str | None = None) -> None:
        self.bot.answer_callback(tap.token, text)


__all__ = ["TelegramError", "TelegramReview", "who"]
