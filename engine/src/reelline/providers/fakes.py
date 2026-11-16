"""Provider fakes for step tests: no SDK, no network, no key.

FakeLLM(replies) answers complete() / complete_json() / look() with the next reply in order (a str
is the text; a dict is JSON, its dumps the text; an exception is raised), records every request in
.requests (role, method, purpose, messages or prompt, schema, ...) and writes the answer to the
ledger like a real adapter (usage, cost from models.prices for the role's model). One FakeLLM serves
every role: for_role() shares the replies and the requests.

FakeTTS(wav) answers synthesize() with that WAV (or a generated silent one), recording each
request; usage `tokens` per call.
"""

from __future__ import annotations

import io
import json
import wave
from typing import Any

from reelline.providers.base import LLMReply, LLMUsage, Speech, follow_up
from reelline.providers.costs import NO_LEDGER, CostLedger, cost_usd

_DEFAULT_USAGE = LLMUsage(100, 50, 0)


class FakeLLM:
    def __init__(
        self,
        replies: list[Any] | None = None,
        *,
        usage: LLMUsage = _DEFAULT_USAGE,
        role: str = "writer",
        _shared: dict | None = None,
    ):
        from reelline.providers.registry import spec

        self.role, self.spec = role, spec(role)
        self._shared = (
            _shared if _shared is not None else {"replies": list(replies or []), "requests": [], "usage": usage}
        )

    @property
    def requests(self) -> list[dict]:
        return self._shared["requests"]

    def for_role(self, role: str) -> FakeLLM:
        return FakeLLM(role=role, _shared=self._shared)

    def _answer(
        self, method: str, ledger: CostLedger | None, purpose: str, prompt_version: int, items: int, **request
    ) -> LLMReply:
        self.requests.append({"role": self.role, "method": method, "purpose": purpose, **request})
        if not self._shared["replies"]:
            raise AssertionError(f"FakeLLM has no reply left for {self.role}/{purpose}")
        answer = self._shared["replies"].pop(0)
        if isinstance(answer, BaseException):
            raise answer
        data = answer if isinstance(answer, dict) else None
        text = json.dumps(answer, ensure_ascii=False) if data is not None else str(answer)
        usage, model = self._shared["usage"], self.spec.model
        cost = cost_usd(model, usage)
        call_id, row = (ledger or NO_LEDGER).answered(
            purpose, model, prompt_version, usage, served=model, cost=cost, attempt=1, items=items
        )
        return LLMReply(text, usage, model, cost, call_id, data, row=row)

    def complete(
        self, messages, *, purpose: str, prompt_version: int, system=None, schema=None, ledger=None, items: int = 1
    ) -> LLMReply:
        return self._answer(
            "complete", ledger, purpose, prompt_version, items, messages=messages, system=system, schema=schema
        )

    def complete_json(
        self, prompt: str, schema: dict, *, purpose: str, prompt_version: int, ledger=None, items: int = 1
    ) -> LLMReply:
        reply = self._answer("complete_json", ledger, purpose, prompt_version, items, prompt=prompt, schema=schema)
        return (
            reply
            if reply.data is not None
            else LLMReply(
                reply.text,
                reply.usage,
                reply.model,
                reply.cost_usd,
                reply.call_id,
                json.loads(reply.text),
                row=reply.row,
            )
        )

    def look(
        self,
        image: bytes,
        prompt: str,
        schema: dict,
        *,
        purpose: str,
        prompt_version: int,
        mime: str = "image/jpeg",
        ledger=None,
    ) -> LLMReply:
        return self._answer("look", ledger, purpose, prompt_version, 1, image=image, prompt=prompt, schema=schema)

    def follow_up(self, messages: list[dict], reply: LLMReply, user_text: str) -> list[dict]:
        return follow_up(messages, reply, user_text)


def silent_wav(seconds: float = 0.1, rate: int = 24000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * round(seconds * rate))
    return out.getvalue()


class FakeTTS:
    def __init__(
        self,
        wav: bytes | None = None,
        *,
        tokens: tuple[int, int] = (10, 100),
        role: str = "tts",
        _requests: list | None = None,
    ):
        from reelline.providers.registry import spec

        self.role, self.spec = role, spec(role)
        self.wav, self.tokens = wav or silent_wav(), tokens
        self.requests: list[dict] = _requests if _requests is not None else []

    def for_role(self, role: str) -> FakeTTS:
        return FakeTTS(self.wav, tokens=self.tokens, role=role, _requests=self.requests)

    def synthesize(self, text: str, *, voice: str, style: str) -> Speech:
        self.requests.append({"text": text, "voice": voice, "style": style})
        return Speech(self.wav, *self.tokens)
