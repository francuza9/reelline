"""Slack: a Notifier over an incoming webhook (api.slack.com/messaging/webhooks).

notify.slack: webhook_url_env (the env variable with the hook's URL; a secret), timeout_s,
text_max_chars. An incoming webhook posts only: no edits, no threads, no taps, so a button is
written under the text ("[🔁 Retry] j:5:rt": the action to give `reelline review tap`), an edit is
posted as a new message, and a file goes as a line naming it (a webhook can't upload).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from reelline.config import conf
from reelline.notify.base import Buttons, MessageRef, NotifyError, buttons_text


class SlackError(NotifyError):
    """A webhook post that failed."""


class SlackNotifier:
    name = "slack"

    def __init__(self, url: str | None = None, http: httpx.Client | None = None):
        from reelline.config.credentials import load_env_value

        s = conf().notify.slack
        self.url = url or load_env_value(s.webhook_url_env)
        self._http = http or httpx.Client(timeout=s.timeout_s)

    @property
    def text_limit(self) -> int:
        return conf().notify.slack.text_max_chars

    def _post(self, text: str) -> None:
        try:
            response = self._http.post(self.url, json={"text": text})
        except httpx.HTTPError as exc:
            raise SlackError(f"Slack webhook: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise SlackError(f"Slack webhook: HTTP {response.status_code}: {response.text[:200]}")

    def send(
        self, text: str, buttons: Buttons | None = None, *, reply_to: MessageRef | None = None
    ) -> MessageRef | None:
        extra = buttons_text(buttons)
        self._post(f"{text}\n{extra}" if extra else text)
        return None

    def edit(self, ref: MessageRef, text: str, buttons: Buttons | None = None) -> None:
        self.send(text, buttons)

    def edit_buttons(self, ref: MessageRef, buttons: Buttons) -> None:
        if buttons:
            self.send(buttons_text(buttons))

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
        where = f" ({data})" if isinstance(data, Path) else ""
        self._post(f"📎 {kind} {filename}{where}" + (f"\n{caption}" if caption else ""))
        return None
