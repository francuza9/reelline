"""A webhook as the review channel: every message POSTed as JSON to review.webhook.url, decisions
polled from review.webhook.poll_url (the simplest robust way back: no port to open, a restart loses
nothing, the cursor is kept by the bot like Telegram's update offset). `reelline review tap|say`
(the inbox, reelline/review/inbox.py) works too.

Out (POST url; header Authorization: Bearer <REVIEW_WEBHOOK_TOKEN> when set):
  {"type": "message", "ref": 7, "text": "...", "actions": [[{"label": "✅ Approve", "data": "r:12:ap"}]],
   "reply_to": null}
  {"type": "review", "ref": 8, "text": <status block>, "actions": [...], "review": {"review_id": 12,
   "reel_id": 30, "slot_date": "2026-10-01", "slot_time": "13:00", "caption": "...", "video": "/path",
   "flags": {"factcheck": [...], "qa": [...]}}}
  {"type": "edit", "ref": 8, "text": "...", "actions": [...]}     {"type": "buttons", "ref": 8, "actions": [...]}
  {"type": "video", "ref": 9, "path": "/path/reel.mp4", "text": <caption>}
  {"type": "file", "ref": 10, "kind": "document", "filename": "x.csv", "mime": "text/csv",
   "data_b64": "...", "text": <caption>}
  {"type": "ack", "token": "<event id>", "text": "approved"}
A 2xx reply is success; a JSON reply's "ref" replaces the local one (the receiver's message id).

In (GET poll_url?cursor=<cursor>&timeout=<s>):
  {"events": [{"id": "e1", "type": "tap", "data": "r:12:ap", "user": "ana"},
              {"id": "e2", "type": "command", "name": "status", "args": [], "user": "ana"},
              {"id": "e3", "type": "text", "text": "new hook", "user": "ana"}],
   "cursor": "e3"}
The action data is the "data" of the actions sent; the cursor is handed back on the next poll.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from reelline.config import conf
from reelline.notify.base import Buttons, MessageRef, file_bytes
from reelline.review import inbox
from reelline.review.base import Command, Event, Incoming, ReviewError, ReviewInfo, Tap, Text


def actions(rows: Buttons | None) -> list[list[dict]]:
    return [[{"label": label, "data": data} for label, data in row] for row in rows or []]


class WebhookReview:
    name = "webhook"
    text_limit = 100_000

    def __init__(
        self,
        http: httpx.Client | None = None,
        url: str | None = None,
        poll_url: str | None = None,
        token: str | None = None,
        inbox_dir: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        s = conf().review.webhook
        self.url = url or s.url
        self.poll_url = poll_url if poll_url is not None else s.poll_url
        if not self.url:
            raise ReviewError("review.webhook.url is not set")
        if token is None:
            from reelline.config.credentials import _read_env

            token = (_read_env()[0].get(s.token_env) or "").strip() or None
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = http or httpx.Client(timeout=s.timeout_s)
        self.inbox_dir, self.sleep = inbox_dir, sleep
        self._next = 0

    def _post(self, body: dict) -> MessageRef | None:
        if body["type"] != "ack":
            self._next += 1
            body = {"ref": self._next, **body}
        try:
            response = self._http.post(self.url, json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ReviewError(f"review webhook: {type(exc).__name__}") from None
        if not 200 <= response.status_code < 300:
            raise ReviewError(f"review webhook: HTTP {response.status_code}")
        try:
            reply = response.json()
        except ValueError:
            reply = None
        return (reply.get("ref") if isinstance(reply, dict) else None) or body.get("ref")

    # -- sending --

    def send(
        self, text: str, buttons: Buttons | None = None, *, reply_to: MessageRef | None = None
    ) -> MessageRef | None:
        return self._post({"type": "message", "text": text, "actions": actions(buttons), "reply_to": reply_to})

    def edit(self, ref: MessageRef, text: str, buttons: Buttons | None = None) -> None:
        self._post({"type": "edit", "ref": ref, "text": text, "actions": actions(buttons)})

    def edit_buttons(self, ref: MessageRef, buttons: Buttons) -> None:
        self._post({"type": "buttons", "ref": ref, "actions": actions(buttons)})

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
        return self._post(
            {
                "type": "file",
                "kind": kind,
                "filename": filename,
                "mime": mime,
                "text": caption,
                "data_b64": base64.b64encode(file_bytes(data)).decode(),
            }
        )

    def send_review(self, text: str, buttons: Buttons, info: ReviewInfo) -> MessageRef | None:
        return self._post(
            {
                "type": "review",
                "text": text,
                "actions": actions(buttons),
                "review": {
                    "review_id": info.review_id,
                    "reel_id": info.reel_id,
                    "slot_date": info.slot_date,
                    "slot_time": info.slot_time,
                    "caption": info.caption,
                    "video": str(info.video) if info.video else None,
                    "flags": info.flags,
                },
            }
        )

    def send_video(self, path: Path, caption: str) -> None:
        self._post({"type": "video", "path": str(path), "text": caption})

    # -- receiving --

    def parse(self, raw: Any) -> list[Event]:
        if isinstance(raw, Event):
            return [raw]
        kind, user = raw.get("type"), str(raw.get("user") or "?")
        if kind == "tap" and raw.get("data"):
            return [Tap(str(raw["data"]), user, raw.get("ref"), str(raw.get("id") or ""))]
        if kind == "command" and raw.get("name"):
            return [Command(str(raw["name"]).lstrip("/").lower(), tuple(str(a) for a in raw.get("args") or ()), user)]
        if kind == "text" and str(raw.get("text") or "").strip():
            return [Text(str(raw["text"]).strip(), user)]
        return []

    def receive(self, cursor: str | None, timeout: int) -> list[Incoming]:
        got = inbox.take(self.inbox_dir)
        if not self.poll_url:
            if not got:
                self.sleep(timeout)
            return got
        try:
            response = self._http.get(
                self.poll_url,
                params={"cursor": cursor or "", "timeout": timeout},
                headers=self._headers,
                timeout=timeout + conf().review.webhook.timeout_s,
            )
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ReviewError(f"review webhook poll: {type(exc).__name__}") from None
        if response.status_code != 200 or not isinstance(body, dict):
            raise ReviewError(f"review webhook poll: HTTP {response.status_code}")
        events = body.get("events") or []
        for i, raw in enumerate(events):
            parsed = self.parse(raw) if isinstance(raw, dict) else []
            nxt = (
                str(raw.get("id"))
                if i < len(events) - 1 and isinstance(raw, dict) and raw.get("id")
                else body.get("cursor")
            )
            got.append(
                Incoming(
                    str(raw.get("id") if isinstance(raw, dict) else i),
                    parsed,
                    str(nxt) if nxt is not None else cursor,
                    ignored=None if parsed else "an event the channel doesn't know",
                )
            )
        return got

    def ack(self, tap: Tap, text: str | None = None) -> None:
        if tap.token:
            self._post({"type": "ack", "token": tap.token, "text": text})
