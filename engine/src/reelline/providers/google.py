"""Gemini through google-genai (`pip install reelline[google]`): LLMProvider, VisionChecker,
ImageGenerator and TTSProvider.

connect() builds the client: the key named by models.providers.google.api_key_env passed
explicitly, so the SDK never falls back to GEMINI_API_KEY / GOOGLE_API_KEY / ADC; vertexai=False
stops GOOGLE_GENAI_USE_VERTEXAI in the shell from switching backends; timeout_s per request, every
call of the client (TTS through interactions too): a connection the server dropped raises after it
instead of waiting forever.

complete_json() (assess, brief, fact-check) sends one prompt with a JSON response schema and the
role's temperature, seed and thinking level, and returns the parsed object (an LLMError when it
isn't JSON or isn't an object). Every call goes to the ledger, before its JSON is parsed. A dropped
connection (google-genai re-raises httpx's errors as they are) is tried again
(reelline/providers/retry.py: 3 tries in all, 5 s then 20 s apart); each failed try is an llm_calls
row with its error and try number (no tokens or cost: nothing came back), and after the last one
TransportFailure names the call ("factcheck (gemini-3.8-flash) ReadTimeout after 3 tries: ...").
429 / 5xx answers keep their own retries (the role's api_retries); a 4xx is an LLMError at once.

look() (the image picker's check, QA's frames) sends the image and a prompt with a JSON schema; an
API error or unparsable JSON is an LLMError, and the cost row is recorded only once the answer
parsed. generate() (the fallback illustration) prices image output tokens at the image rate when
the usage breaks them out, else every output token as image (purpose "<purpose>_upper_bound").
synthesize() is one TTS call: WAV bytes and the token usage. These make one try (their roles'
transport_retry: false), as a render's calls always did.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from reelline.providers.base import Adapter, GeneratedImage, LLMError, LLMReply, LLMUsage, Speech, TTSError, as_messages
from reelline.providers.costs import NO_LEDGER, CostLedger, cost_usd
from reelline.providers.retry import error_text, with_retries, with_status_retries


def connect(settings) -> Any:
    from google import genai
    from google.genai import types

    from reelline.config.credentials import load_gemini

    options = types.HttpOptions(timeout=int(settings.timeout_s * 1000)) if settings.timeout_s else None
    if settings.base_url:
        options = (options or types.HttpOptions()).model_copy(update={"base_url": settings.base_url})
    return genai.Client(api_key=load_gemini().gemini_api_key, vertexai=False, http_options=options)


def usage_of(response) -> LLMUsage:
    """generate_content's usage_metadata (None fields when it has none)."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return LLMUsage()
    return LLMUsage(meta.prompt_token_count, meta.candidates_token_count, getattr(meta, "thoughts_token_count", None))


class GoogleProvider(Adapter):
    kind = "google"
    connect = staticmethod(connect)

    def _config(self, schema: dict | None):
        from google.genai import types

        s = self.spec
        kw: dict[str, Any] = {"temperature": s.temperature, "seed": s.seed}
        if schema is not None:
            kw.update(response_mime_type="application/json", response_json_schema=schema)
        if s.thinking:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=s.thinking)
        if s.max_tokens:
            kw["max_output_tokens"] = s.max_tokens
        return types.GenerateContentConfig(
            **kw, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )

    def _generate(self, contents, config, *, purpose: str, prompt_version: int, ledger: CostLedger, items: int):
        """generate_content with both retry layers, every failed try recorded: (response, try)."""
        from google.genai import errors

        model = self.spec.model

        def failed(attempt: int, exc: BaseException) -> None:  # nothing came back: no tokens, no cost
            ledger.failed(purpose, model, prompt_version, error_text(exc), attempt, items=items)

        try:
            return with_status_retries(
                lambda: with_retries(
                    lambda: self.client.models.generate_content(model=model, contents=contents, config=config),
                    purpose=purpose,
                    model=model,
                    failed=failed,
                    enabled=self.spec.transport_retry,
                ),
                status_of=lambda exc: getattr(exc, "code", None),
                error=errors.APIError,
                retries=self.spec.api_retries,
                wait_s=self.spec.api_retry_wait_s,
                label=f"Gemini {purpose}",
            )
        except errors.APIError as exc:
            raise LLMError(f"Gemini {purpose} call failed: {exc}") from None

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
        ledger = ledger or NO_LEDGER
        response, tries = self._generate(
            prompt, self._config(schema), purpose=purpose, prompt_version=prompt_version, ledger=ledger, items=items
        )
        usage = usage_of(response)
        cost = cost_usd(self.spec.model, usage)
        call_id, _ = ledger.answered(
            purpose, self.spec.model, prompt_version, usage, cost=cost, attempt=tries, items=items
        )
        text = response.text or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Gemini {purpose} returned invalid JSON ({exc}): {text[:300]!r}") from None
        if not isinstance(data, dict):
            raise LLMError(f"Gemini {purpose} returned {type(data).__name__}, not an object")
        return LLMReply(text, usage, self.spec.model, cost, call_id, data)

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
        from google.genai import types

        ledger = ledger or NO_LEDGER
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=_text_of(m["content"]))],
            )
            for m in as_messages(messages)
        ]
        config = self._config(schema)
        if system is not None:
            config = config.model_copy(update={"system_instruction": system})
        response, tries = self._generate(
            contents, config, purpose=purpose, prompt_version=prompt_version, ledger=ledger, items=items
        )
        usage = usage_of(response)
        cost = cost_usd(self.spec.model, usage)
        call_id, _ = ledger.answered(
            purpose, self.spec.model, prompt_version, usage, cost=cost, attempt=tries, items=items
        )
        text = (response.text or "").strip()
        if not text:
            raise LLMError(f"Gemini {purpose} call returned no text")
        return LLMReply(text, usage, self.spec.model, cost, call_id)

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
        from google.genai import errors, types

        ledger = ledger or NO_LEDGER
        part = (
            types.Part.from_bytes(data=image, mime_type=mime, media_resolution=self.spec.media_resolution)
            if self.spec.media_resolution
            else types.Part.from_bytes(data=image, mime_type=mime)
        )
        try:
            response, _ = with_retries(
                lambda: self.client.models.generate_content(
                    model=self.spec.model, contents=[part, prompt], config=self._config(schema)
                ),
                purpose=purpose,
                model=self.spec.model,
                enabled=self.spec.transport_retry,
            )
            text = response.text or ""
            data = json.loads(text)
        except (errors.APIError, json.JSONDecodeError) as exc:
            raise LLMError(str(exc)) from exc
        meta = getattr(response, "usage_metadata", None)
        usage = LLMUsage(
            getattr(meta, "prompt_token_count", None) or 0,
            getattr(meta, "candidates_token_count", None) or 0,
            getattr(meta, "thoughts_token_count", None) or 0,
        )
        cost = cost_usd(self.spec.model, usage)
        call_id, row = ledger.answered(purpose, self.spec.model, prompt_version, usage, cost=cost)
        return LLMReply(text, usage, self.spec.model, cost, call_id, data, row=row)

    def generate(
        self,
        prompt: str,
        *,
        seed: int,
        purpose: str = "image_generate",
        prompt_version: int = 1,
        ledger: CostLedger | None = None,
    ) -> GeneratedImage | None:
        from google.genai import errors

        ledger, s = ledger or NO_LEDGER, self.spec
        try:
            interaction, _ = with_retries(
                lambda: self.client.interactions.create(
                    model=s.model,
                    input=prompt,
                    response_format={
                        "type": "image",
                        "mime_type": "image/jpeg",
                        "aspect_ratio": s.aspect_ratio,
                        "image_size": s.image_size,
                    },
                    generation_config={"seed": seed},
                ),
                purpose=purpose,
                model=s.model,
                enabled=s.transport_retry,
            )
        except errors.APIError as exc:
            raise LLMError(str(exc)) from exc
        usage, bounded = interaction_image_usage(interaction.usage)
        ledger.answered(
            purpose if bounded else f"{purpose}_upper_bound",
            s.model,
            prompt_version,
            usage,
            cost=cost_usd(s.model, usage),
        )
        if interaction.output_image is None or not interaction.output_image.data:
            return None
        return GeneratedImage(base64.b64decode(interaction.output_image.data), "image/jpeg", s.model)

    def synthesize(self, text: str, *, voice: str, style: str) -> Speech:
        interaction, _ = with_retries(
            lambda: self.client.interactions.create(
                model=self.spec.model,
                input=[
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                                "annotations": [{"type": "speech_metadata", "style": style}],
                            }
                        ],
                    }
                ],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice}]},
            ),
            purpose="tts",
            model=self.spec.model,
            enabled=self.spec.transport_retry,
        )
        if interaction.output_audio is None or not interaction.output_audio.data:
            raise TTSError(f"No audio in the TTS response for: {text[:40]!r}...")
        audio = base64.b64decode(interaction.output_audio.data)
        if not audio.startswith(b"RIFF"):
            raise TTSError(f"TTS audio isn't WAV (first bytes {audio[:4]!r}, {len(audio)} bytes)")
        usage = interaction.usage
        return Speech(
            wav=audio,
            input_tokens=(usage.total_input_tokens or 0) if usage else 0,
            output_tokens=(usage.total_output_tokens or 0) if usage else 0,
        )


def interaction_image_usage(usage) -> tuple[LLMUsage, bool]:
    """(usage, whether the image tokens are known) of an image interaction: image output tokens from
    output_tokens_by_modality; without the breakdown every output and thinking token counts as image
    (the upper bound)."""
    tokens = {
        k: (getattr(usage, f"total_{k}_tokens", None) or 0) if usage else 0 for k in ("input", "output", "thought")
    }
    by_modality = getattr(usage, "output_tokens_by_modality", None) if usage else None
    if by_modality:
        image = sum(m.tokens or 0 for m in by_modality if getattr(m.modality, "value", m.modality) == "image")
        known = True
    else:
        image, known = tokens["output"] + tokens["thought"], False
    return LLMUsage(tokens["input"], tokens["output"], tokens["thought"], image), known


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(getattr(b, "text", None) or (b.get("text", "") if isinstance(b, dict) else "") for b in content)
