"""Any OpenAI-compatible chat completions endpoint (OpenAI, OpenRouter, vLLM, Ollama, LM Studio, ...):
an LLMProvider and a VisionChecker, over httpx (no SDK; `reelline[openai]` adds nothing).

connect() builds an httpx.Client on models.providers.openai.base_url with the key named by its
api_key_env (Bearer; a local endpoint may run without one) and its timeout. complete() posts
/chat/completions: the role's model, messages (a system prompt first), max_completion_tokens,
temperature, seed, reasoning_effort (the role's effort), and a schema as
response_format={"type": "json_schema", "json_schema": {"name": ..., "schema": ..., "strict": true}}.
The answer's refusal (message.refusal) is a RefusalError, retried once on the role's
refusal_fallback; finish_reason "length" or an empty answer is an LLMError. Usage: prompt_tokens,
completion_tokens (thinking included, so output = completion - reasoning_tokens) and
completion_tokens_details.reasoning_tokens. A dropped connection is tried again
(reelline/providers/retry.py); a 429 / 5xx too when the role has api_retries; any other HTTP error is
an LLMError at once, with the endpoint's message (never the key).
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from reelline.providers.base import Adapter, LLMError, LLMReply, LLMUsage, RefusalError, as_messages
from reelline.providers.costs import NO_LEDGER, CostLedger, cost_usd
from reelline.providers.retry import error_text, with_retries, with_status_retries


class HTTPStatusError(LLMError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def connect(settings) -> httpx.Client:
    from reelline.config.credentials import load_optional

    key = load_optional(settings.api_key_env)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return httpx.Client(base_url=settings.base_url.rstrip("/") + "/", headers=headers, timeout=settings.timeout_s)


def usage_of(body: dict) -> LLMUsage:
    u = body.get("usage") or {}
    if not u:
        return LLMUsage()
    reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    return LLMUsage(u.get("prompt_tokens") or 0, (u.get("completion_tokens") or 0) - reasoning, reasoning)


class OpenAIProvider(Adapter):
    kind = "openai"
    connect = staticmethod(connect)

    def _body(self, model: str, messages: list[dict], schema: dict | None, purpose: str) -> dict:
        s = self.spec
        body: dict[str, Any] = {"model": model, "messages": messages}
        if s.max_tokens:
            body["max_completion_tokens"] = s.max_tokens
        if s.temperature is not None:
            body["temperature"] = s.temperature
        if s.seed is not None:
            body["seed"] = s.seed
        if s.effort:
            body["reasoning_effort"] = s.effort
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": purpose, "schema": schema, "strict": True},
            }
        return body

    def _post(
        self, body: dict, *, purpose: str, prompt_version: int, ledger: CostLedger, items: int
    ) -> tuple[dict, int]:
        model = body["model"]

        def once() -> dict:
            response = self.client.post("chat/completions", json=body)
            if response.status_code >= 400:
                try:
                    message = response.json().get("error", {}).get("message") or response.text
                except ValueError:
                    message = response.text
                raise HTTPStatusError(
                    response.status_code, f"{purpose} call failed: HTTP {response.status_code}: {message[:300]}"
                )
            return response.json()

        def failed(attempt: int, exc: BaseException) -> None:
            ledger.failed(purpose, model, prompt_version, error_text(exc), attempt, items=items)

        return with_status_retries(
            lambda: with_retries(once, purpose=purpose, model=model, failed=failed, enabled=self.spec.transport_retry),
            status_of=lambda exc: getattr(exc, "status", None),
            error=HTTPStatusError,
            retries=self.spec.api_retries,
            wait_s=self.spec.api_retry_wait_s,
            label=purpose,
        )

    def _ask(
        self,
        model: str,
        messages: list[dict],
        schema: dict | None,
        *,
        purpose: str,
        prompt_version: int,
        ledger: CostLedger,
        items: int,
    ) -> tuple[LLMReply, dict]:
        body, tries = self._post(
            self._body(model, messages, schema, purpose),
            purpose=purpose,
            prompt_version=prompt_version,
            ledger=ledger,
            items=items,
        )
        usage = usage_of(body)
        served = body.get("model") or model
        cost = cost_usd(served, usage)
        call_id, row = ledger.answered(
            purpose, model, prompt_version, usage, served=served, cost=cost, attempt=tries, items=items
        )
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return LLMReply(
            (message.get("content") or "").strip(),
            usage,
            served,
            cost,
            call_id,
            content=message.get("content"),
            row=row,
        ), choice

    def complete(
        self,
        messages: list[dict] | str,
        *,
        purpose: str,
        prompt_version: int,
        system: str | None = None,
        schema: dict | None = None,
        ledger: CostLedger | None = None,
        items: int = 1,
    ) -> LLMReply:
        ledger = ledger or NO_LEDGER
        turns = ([{"role": "system", "content": system}] if system is not None else []) + as_messages(messages)
        reply, choice = self._ask(
            self.spec.model, turns, schema, purpose=purpose, prompt_version=prompt_version, ledger=ledger, items=items
        )
        fallback = self.spec.refusal_fallback
        if (choice.get("message") or {}).get("refusal") and fallback:
            print(f"  {purpose}: {self.spec.model} refused; retrying on {fallback}", flush=True)
            reply, choice = self._ask(
                fallback,
                turns,
                schema,
                purpose=f"{purpose}_fallback",
                prompt_version=prompt_version,
                ledger=ledger,
                items=items,
            )
        refusal = (choice.get("message") or {}).get("refusal")
        if refusal:
            chain = f"{self.spec.model} and {fallback}" if fallback else self.spec.model
            raise RefusalError(f"{purpose} call was refused by {chain}: {refusal[:200]}")
        if choice.get("finish_reason") == "length":
            raise LLMError(f"{purpose} call hit max_tokens ({self.spec.max_tokens})")
        if not reply.text:
            raise LLMError(f"{purpose} call returned no text")
        return reply

    def complete_json(
        self,
        prompt: str,
        schema: dict,
        *,
        purpose: str,
        prompt_version: int,
        ledger: CostLedger | None = None,
        items: int = 1,
    ) -> LLMReply:
        reply = self.complete(
            prompt, purpose=purpose, prompt_version=prompt_version, schema=schema, ledger=ledger, items=items
        )
        return _parsed(reply, purpose)

    def look(
        self,
        image: bytes,
        prompt: str,
        schema: dict,
        *,
        purpose: str,
        prompt_version: int,
        mime: str = "image/jpeg",
        ledger: CostLedger | None = None,
    ) -> LLMReply:
        url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        turns = [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": prompt}],
            }
        ]
        reply, _ = self._ask(
            self.spec.model,
            turns,
            schema,
            purpose=purpose,
            prompt_version=prompt_version,
            ledger=ledger or NO_LEDGER,
            items=1,
        )
        return _parsed(reply, purpose)


def _parsed(reply: LLMReply, purpose: str) -> LLMReply:
    try:
        data = json.loads(reply.text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{purpose} returned invalid JSON ({exc}): {reply.text[:300]!r}") from None
    if not isinstance(data, dict):
        raise LLMError(f"{purpose} returned {type(data).__name__}, not an object")
    return LLMReply(reply.text, reply.usage, reply.model, reply.cost_usd, reply.call_id, data, reply.content, reply.row)
