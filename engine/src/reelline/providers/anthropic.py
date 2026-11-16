"""Claude through the Anthropic SDK (`pip install reelline[anthropic]`): an LLMProvider.

connect() builds the client from the key named by models.providers.anthropic.api_key_env (the channel's:
its own variable; reelline/config/credentials.py), passed explicitly as api_key=, with the
configured base URL (the API's own). An explicit api_key stops the SDK from consulting
ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or auth profiles, so nothing in the shell environment
changes where calls go or which key they use.

complete() sends one request with the role's model, its thinking ("adaptive": never a budget; the
model rejects one) and effort, optionally a JSON schema (output_config.format), streamed
(client.messages.stream, then the final message): the SDK refuses a non-streaming request whose
max_tokens could take over 10 minutes (above ~21,333), and the host's max_tokens is above that. A
refusal is retried once, client-side, on the role's refusal_fallback (the same request, recorded as
"<purpose>_fallback"): claude-sonnet-5-5 takes only claude-sonnet-5 as a server-side fallback, so
the API's own fallback can't route to another model. It returns only the text blocks' text;
thinking blocks are ignored. The full content is kept for multi-turn follow-ups (replayed
unchanged, thinking blocks included; a model that didn't write them drops them). A refusal by the
fallback too (RefusalError), a max_tokens stop or an empty answer raise LLMError. A call whose
thinking passes thinking_alert_share of max_tokens is logged here and alerted by the daily tick
(reelline/pipeline/run_daily.py thinking_alerts()). Every call goes to the ledger with input, output
(without thinking) and thinking tokens, its cost (reelline/providers/costs.py) for the model that
answered, and the script it belongs to.

A request whose connection drops, while it's sent or mid-stream, is tried again
(reelline/providers/retry.py: 3 tries in all, 5 s then 20 s apart), each model of a refusal
fallback on its own. Every failed try is a row of its own in llm_calls (error, attempt; the tokens
known when it dropped: the input, once the stream had started, so its cost is a lower bound), and
the call that answers carries its try number. After the last try TransportFailure names the call
("punchup (claude-sonnet-5-5) RemoteProtocolError after 3 tries: ..."); an HTTP error answer (a
4xx) is an LLMError at once.
"""

from __future__ import annotations

import json
from typing import Any

from reelline.providers.base import Adapter, LLMError, LLMReply, LLMUsage, RefusalError, as_messages
from reelline.providers.costs import NO_LEDGER, CostLedger, cost_usd
from reelline.providers.retry import TransportFailure, error_text, with_retries


def connect(settings) -> Any:
    import anthropic

    from reelline.config.credentials import load_anthropic

    return anthropic.Anthropic(api_key=load_anthropic().api_key, base_url=settings.base_url)


def usage_of(response) -> LLMUsage:
    u = response.usage
    details = getattr(u, "output_tokens_details", None)
    thinking = (getattr(details, "thinking_tokens", None) or 0) if details else 0
    cache = (getattr(u, "cache_creation_input_tokens", None) or 0) + (getattr(u, "cache_read_input_tokens", None) or 0)
    return LLMUsage((u.input_tokens or 0) + cache, (u.output_tokens or 0) - thinking, thinking)


class AnthropicProvider(Adapter):
    kind = "anthropic"
    connect = staticmethod(connect)

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
        s, ledger = self.spec, ledger or NO_LEDGER
        output_config: dict[str, Any] = {"effort": s.effort} if s.effort else {}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        request: dict[str, Any] = {"max_tokens": s.max_tokens}
        if s.thinking:
            request["thinking"] = {"type": s.thinking}
        if output_config:
            request["output_config"] = output_config
        if system is not None:
            request["system"] = system
        request["messages"] = as_messages(messages)

        response, served, usage, cost, call_id = self._call(s.model, request, purpose, prompt_version, ledger, items)
        fallback = s.refusal_fallback
        if response.stop_reason == "refusal" and fallback:
            print(
                f"  {purpose}: {s.model} refused (category {_refusal_category(response)}); retrying on {fallback}",
                flush=True,
            )
            response, served, usage, cost, call_id = self._call(
                fallback, request, f"{purpose}_fallback", prompt_version, ledger, items
            )

        if response.stop_reason == "refusal":
            chain = f"{s.model} and {fallback}" if fallback else s.model
            raise RefusalError(f"Claude {purpose} call was refused by {chain} (category {_refusal_category(response)})")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"Claude {purpose} call hit max_tokens ({s.max_tokens})")
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()
        if not text:
            raise LLMError(f"Claude {purpose} call returned no text")
        return LLMReply(text, usage, served, cost, call_id, content=list(response.content))

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
        try:
            data = json.loads(reply.text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Claude {purpose} returned invalid JSON ({exc}): {reply.text[:300]!r}") from None
        if not isinstance(data, dict):
            raise LLMError(f"Claude {purpose} returned {type(data).__name__}, not an object")
        return LLMReply(reply.text, reply.usage, reply.model, reply.cost_usd, reply.call_id, data, reply.content)

    def _call(self, model: str, request: dict, purpose: str, prompt_version: int, ledger: CostLedger, items: int):
        """One request to `model`, tried again when its connection drops (reelline/providers/retry.py),
        every try recorded; (response, served model, usage, cost, call id)."""
        current: dict = {}

        def once():
            current.clear()
            with self.client.messages.stream(model=model, **request) as stream:
                current["stream"] = stream
                return stream.get_final_message()

        def failed(attempt: int, exc: BaseException) -> None:
            usage, served = _so_far(current.get("stream"))
            ledger.failed(
                purpose,
                model,
                prompt_version,
                error_text(exc),
                attempt,
                usage=usage,
                served=served or model,
                cost=cost_usd(served or model, usage) if usage else None,
                items=items,
            )

        try:
            response, attempt = with_retries(
                once, purpose=purpose, model=model, failed=failed, enabled=self.spec.transport_retry
            )
        except TransportFailure:
            raise
        except Exception as exc:  # the SDK's typed errors; the key is never in their text
            import anthropic

            if isinstance(exc, anthropic.APIError):
                raise LLMError(f"Claude {purpose} call failed: {type(exc).__name__}: {exc}") from None
            raise

        usage = usage_of(response)
        served = getattr(response, "model", None) or model
        share, ceiling = self.spec.thinking_alert_share, self.spec.max_tokens
        if share is not None and ceiling and usage.thinking > share * ceiling:
            print(
                f"  warning: {purpose} thought {usage.thinking} tokens of max_tokens {ceiling} "
                f"(the daily tick alerts it)",
                flush=True,
            )
        cost = cost_usd(served, usage)
        if cost is None:
            print(f"  warning: no price for {served} in models.prices; cost not recorded", flush=True)
        call_id, _ = ledger.answered(
            purpose, model, prompt_version, usage, served=served, cost=cost, attempt=attempt, items=items
        )
        return response, served, usage, cost, call_id


def _so_far(stream) -> tuple[LLMUsage | None, str | None]:
    """(usage, model) a dropped stream had reported (its start carries the input
    tokens; the output it had made isn't known yet), or (None, None) before its start."""
    try:
        snapshot = stream.current_message_snapshot if stream is not None else None
    except AssertionError:  # no event received
        return None, None
    if snapshot is None or getattr(snapshot, "usage", None) is None:
        return None, None
    return usage_of(snapshot), getattr(snapshot, "model", None)


def _refusal_category(response) -> str | None:
    details = getattr(response, "stop_details", None)
    return getattr(details, "category", None) if details else None
