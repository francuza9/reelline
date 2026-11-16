"""The model providers, the roles (one per use), their prices and the retry."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from .base import Number, Section, env

# ---- models ----------------------------------------------------------------------------------


class ProviderConf(Section):
    """One provider connection (reelline/providers/): which adapter, its endpoint and its key."""

    # anthropic | google | openai (any OpenAI-compatible endpoint).
    type: Literal["anthropic", "google", "openai"]
    # The API's base URL; None: the adapter's own (the vendor's public API).
    base_url: str | None = None
    # Per request, seconds (google: every call of the client; openai: every request).
    timeout_s: float | None = None


class AnthropicConf(ProviderConf):
    type: Literal["anthropic", "google", "openai"] = "anthropic"
    base_url: str | None = "https://api.anthropic.com"
    api_key_env: str = env(
        "REELLINE_ANTHROPIC_API_KEY",
        "Anthropic API key (console.anthropic.com > API keys). Never ANTHROPIC_API_KEY: "
        "that name is refused, so the SDK can't pick a key up by itself.",
    )


class GoogleConf(ProviderConf):
    type: Literal["anthropic", "google", "openai"] = "google"
    timeout_s: float | None = 120
    api_key_env: str = env("GEMINI_API_KEY", "Gemini API key (aistudio.google.com > Get API key).")


class OpenAIConf(ProviderConf):
    type: Literal["anthropic", "google", "openai"] = "openai"
    base_url: str | None = "https://api.openai.com/v1"
    timeout_s: float | None = 120
    api_key_env: str = env(
        "OPENAI_API_KEY",
        "API key of the OpenAI-compatible endpoint (models.providers.openai"
        ".base_url); only a role on provider openai needs it.",
        optional=True,
    )


class Providers(Section):
    anthropic: AnthropicConf = AnthropicConf()
    google: GoogleConf = GoogleConf()
    openai: OpenAIConf = OpenAIConf()


class ModelRole(Section):
    """One use of a model (`models.<role>`): which provider (a models.providers key), which model, and
    its parameters. A parameter a provider doesn't take is left None."""

    provider: Literal["anthropic", "google", "openai"]
    model: str
    # Covers thinking too (anthropic, openai).
    max_tokens: int | None = None
    temperature: float | None = None
    seed: int | None = None
    # anthropic: "adaptive" (thinking={"type": "adaptive"}) or None; google: the thinking level
    # ("low", ...); openai: unused (effort is its reasoning_effort).
    thinking: str | None = None
    # anthropic: output_config.effort; openai: reasoning_effort.
    effort: str | None = None
    # A request the safety classifiers decline is re-sent once to this model (anthropic; None: no retry).
    refusal_fallback: str | None = None
    # A call whose thinking takes more than this share of max_tokens is logged (and alerted by the tick).
    thinking_alert_share: float | None = None
    # A dropped connection is tried again (models.retry); false: one try (a render's calls).
    transport_retry: bool = True
    # An HTTP 429 / 5xx answer is tried again this many times, api_retry_wait_s apart (google).
    api_retries: int = 0
    api_retry_wait_s: float = 0.0
    # google image input: the media_resolution of the image part (None: the API's default).
    media_resolution: str | None = None


class ImageRole(ModelRole):
    """An image generation role (google): the output's aspect ratio and size."""

    aspect_ratio: str = "1:1"
    image_size: str = "1K"


class TtsRole(ModelRole):
    voice_id_env: str = env(
        "TTS_VOICE_ID", "The channel's stored Gemini TTS voice ID (the channel's voice/ notes).", secret=False
    )


class Retry(Section):
    """A call whose connection drops is tried `attempts` times in all, waiting waits_s[n - 1] after
    try n (reelline/providers/retry.py)."""

    attempts: int = 3
    waits_s: tuple[Number, ...] = (5, 20)


_CLAUDE: dict[str, Any] = {
    "provider": "anthropic",
    "model": "claude-sonnet-5-5",
    "max_tokens": 32000,
    "thinking": "adaptive",
    "effort": "high",
    "thinking_alert_share": 0.75,
}
_GEMINI: dict[str, Any] = {
    "provider": "google",
    "model": "gemini-3.8-flash",
    "temperature": 0.0,
    "seed": 0,
    "thinking": "low",
    "api_retries": 2,
    "api_retry_wait_s": 10.0,
}
_GEMINI_VISION: dict[str, Any] = {
    "provider": "google",
    "model": "gemini-3.8-flash",
    "temperature": 0.0,
    "seed": 0,
    "thinking": "low",
    "transport_retry": False,
}

LLM_ROLES = ("writer", "stylist", "proofreader", "caption", "endcard", "assessor", "briefer", "checker")
"""The text roles (get_llm()); vision, qa_vision (get_vision()), illustrator (get_image_generator()),
tts (get_tts()) are the others."""


class Models(Section):
    """Every model the engine calls, one role per use (reelline/providers/registry.py get_llm(role)):
    writer       the script's story pass, its retry, the trial, the repair rounds (reelline/pipeline/script.py)
    stylist      the punch-up pass (and the flirt line's)
    proofreader  the proofread of the script and of the caption's hook
    caption      the caption (hook, title, hashtags)
    endcard      the end card's lines
    assessor     the pick's story assessment (reelline/pipeline/assess.py, techpick.py)
    briefer      the news brief (reelline/pipeline/brief.py)
    checker      the fact-check of the script, its fx graphics and the end card (factcheck.py)
    vision       the image picker's check of a candidate (reelline/media/images/vision.py)
    qa_vision    QA's look at the rendered frames (reelline/pipeline/qa.py)
    illustrator  the generated fallback image (reelline/media/images/sources.py)
    tts          the voice (reelline/media/tts.py, scripts/render_reel.py)"""

    providers: Providers = Providers()
    writer: ModelRole = ModelRole(**_CLAUDE)
    stylist: ModelRole = ModelRole(**_CLAUDE)
    proofreader: ModelRole = ModelRole(**_CLAUDE)
    caption: ModelRole = ModelRole(**_CLAUDE)
    endcard: ModelRole = ModelRole(**_CLAUDE)
    assessor: ModelRole = ModelRole(**_GEMINI)
    briefer: ModelRole = ModelRole(**_GEMINI)
    checker: ModelRole = ModelRole(**_GEMINI)
    vision: ModelRole = ModelRole(**_GEMINI_VISION)
    qa_vision: ModelRole = ModelRole(**_GEMINI_VISION)
    illustrator: ImageRole = ImageRole(provider="google", model="gemini-3.1-flash-image", transport_retry=False)
    tts: TtsRole = TtsRole(provider="google", model="gemini-3.8-flash-tts", transport_retry=False)
    # USD per 1M tokens: input, output (thinking is billed as output), output_image (image models).
    # Every role's model and refusal fallback must be listed (reelline/providers/costs.py).
    prices: dict[str, dict[str, float]] = {
        "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
        "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
        "gemini-3.1-flash-image": {"input": 0.50, "output": 3.0, "output_image": 60.0},
        "gemini-3.8-flash-tts": {"input": 0.50, "output": 9.00},
    }
    retry: Retry = Retry()

    @model_validator(mode="before")
    @classmethod
    def _role_defaults(cls, data: Any) -> Any:
        """A role given in part (`models.writer: {effort: max}`) keeps the rest of its default, unless
        it names another provider (then it's written whole)."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, field in cls.model_fields.items():
            default, given = field.default, data.get(name)
            if (
                isinstance(default, ModelRole)
                and isinstance(given, dict)
                and given.get("provider", default.provider) == default.provider
            ):
                data[name] = {**default.model_dump(exclude_defaults=True), **given}
        return data

    def roles(self) -> dict[str, ModelRole]:
        return {
            name: getattr(self, name) for name in type(self).model_fields if isinstance(getattr(self, name), ModelRole)
        }

    @model_validator(mode="after")
    def _priced(self) -> Models:
        for name, role in self.roles().items():
            for model in (role.model, role.refusal_fallback):
                if model and model not in self.prices:
                    raise ValueError(f"{model} has no price in models.prices (models.{name})")
        return self
