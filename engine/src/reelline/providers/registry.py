"""Roles -> providers: get_llm("writer"), get_vision("vision"), get_image_generator(), get_tts().

A role's config (`models.<role>`: provider, model, parameters) picks the adapter
(reelline/providers/anthropic.py, google.py, openai.py); its connection (`models.providers.<name>`:
base URL, timeout, the key's env variable) is built by connection(role), from the env file each
time, as the old claude_client()/gemini_client() were (a long-running bot picks up a new key).
Adapters import their SDK lazily, so only the providers a channel uses need theirs installed
(`pip install reelline[anthropic,google,openai]`).

`given` (a step's `client` argument) lets a caller hand in what to use instead:
- a provider (an adapter, a FakeLLM): rebound to the role (its for_role(): the same connection
  with the role's settings; a provider of another type gives way to the role's own);
- anything else (a vendor SDK client or a test's fake of one): the connection of the role's
  configured adapter;
- None: the role's adapter on a new connection.
"""

from __future__ import annotations

from typing import Any, cast

from reelline.config import conf
from reelline.config.schema import ModelRole
from reelline.providers.base import ImageGenerator, LLMProvider, Provider, TTSProvider, VisionChecker


def _adapter(kind: str) -> Any:
    if kind == "anthropic":
        from reelline.providers.anthropic import AnthropicProvider

        return AnthropicProvider
    if kind == "google":
        from reelline.providers.google import GoogleProvider

        return GoogleProvider
    if kind == "openai":
        from reelline.providers.openai import OpenAIProvider

        return OpenAIProvider
    raise ValueError(f"no adapter for provider type {kind!r}")


def spec(role: str) -> ModelRole:
    roles = conf().models.roles()
    if role not in roles:
        raise ValueError(f"no model role {role!r} (models.{role}); roles: {', '.join(roles)}")
    return roles[role]


def _new_client(provider: str) -> Any:
    settings = getattr(conf().models.providers, provider)
    return _adapter(settings.type).connect(settings)


def provider(role: str, given: Any = None) -> Provider:
    s = spec(role)
    if isinstance(given, Provider):
        return given.for_role(role)
    settings = getattr(conf().models.providers, s.provider)
    client = given if given is not None else _new_client(s.provider)
    made: Provider = _adapter(settings.type)(role, s, client)
    return made


def get_llm(role: str, given: Any = None) -> LLMProvider:
    return cast(LLMProvider, provider(role, given))


def get_vision(role: str = "vision", given: Any = None) -> VisionChecker:
    return cast(VisionChecker, provider(role, given))


def get_image_generator(role: str = "illustrator", given: Any = None) -> ImageGenerator:
    return cast(ImageGenerator, provider(role, given))


def get_tts(role: str = "tts", given: Any = None) -> TTSProvider:
    return cast(TTSProvider, provider(role, given))


def connection(role: str) -> Any:
    """A new connection (vendor client) of the role's provider: what a client factory hands to the
    steps (reelline/pipeline/pick.py, the images Picker, QA, the script chain)."""
    return _new_client(spec(role).provider)
