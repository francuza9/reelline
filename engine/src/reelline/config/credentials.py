"""Credentials: read from the channel's env file, by the names its config gives (`*_env` fields).

The env file is the only source of truth for credentials. Values are parsed with dotenv_values()
and never read from, or written to, os.environ. Which file: REELLINE__STORAGE__ENV_FILE (absolute;
the deprecated env-file variable too), else storage.env_file in the config, else <channel>/.env if it
exists, else <repo>/.env (reelline/config/__init__.py).

Each service has its own typed accessor (load_gemini(), load_telegram(), ...), which requires only
that service's keys; a missing or empty key raises ConfigError naming the key, and only when the
feature that needs it runs. load_settings() is the TTS pipeline's set (Gemini key + the voice).
Secret fields are repr=False so settings objects never print their values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from reelline.config import ConfigError, conf, default_env_file, env_file_from


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(repr=False)
    voice_id: str
    env_file: Path


@dataclass(frozen=True)
class GeminiSettings:
    gemini_api_key: str = field(repr=False)
    env_file: Path


@dataclass(frozen=True)
class AnthropicSettings:
    api_key: str = field(repr=False)
    env_file: Path


@dataclass(frozen=True)
class MetaSettings:
    app_id: str
    app_secret: str = field(repr=False)
    page_id: str
    page_token: str = field(repr=False)
    env_file: Path


@dataclass(frozen=True)
class BufferSettings:
    api_key: str = field(repr=False)
    org_id: str
    channel_instagram: str
    channel_tiktok: str
    channel_youtube: str
    env_file: Path


@dataclass(frozen=True)
class YouTubeSettings:
    """All optional (reelline/pipeline/stats.py switches each source on when its keys are there):
    the Data API key, and the OAuth client + the channel owner's refresh token for the
    Analytics API (python -m reelline.pipeline.stats_auth youtube)."""

    api_key: str | None = field(repr=False)
    client_id: str | None
    client_secret: str | None = field(repr=False)
    refresh_token: str | None = field(repr=False)
    env_file: Path

    @property
    def analytics(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str = field(repr=False)
    chat_id: str
    env_file: Path


@dataclass(frozen=True)
class PexelsSettings:
    api_key: str = field(repr=False)
    env_file: Path


@dataclass(frozen=True)
class PixabaySettings:
    api_key: str = field(repr=False)
    env_file: Path


@dataclass(frozen=True)
class R2Settings:
    account_id: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    endpoint: str
    bucket: str
    env_file: Path
    public_url: str | None = None  # the bucket's public r2.dev base URL (optional)


@dataclass(frozen=True)
class CloudflareSettings:
    api_token: str = field(repr=False)
    account_id: str
    env_file: Path


@dataclass(frozen=True)
class FreesoundSettings:
    api_key: str = field(repr=False)
    env_file: Path


def resolve_env_file() -> Path:
    """Read on every call: a process env override set after the config loaded still counts."""
    return env_file_from(os.environ) or conf().storage.env_file or default_env_file(conf().channel.dir)


def _read_env() -> tuple[dict[str, str | None], Path]:
    """Every key in the env file (values unstripped), and the file's path."""
    env_file = resolve_env_file()
    if not env_file.is_file():
        raise ConfigError(f"Env file not found: {env_file}")
    # interpolate=False: otherwise ${VAR} references would be filled from os.environ.
    return dotenv_values(env_file, interpolate=False), env_file


def _require(*keys: str) -> tuple[dict[str, str], Path]:
    """The given keys' stripped values from the env file, and the file's path.

    Raises ConfigError naming every missing or empty key (never a value).
    """
    values, env_file = _read_env()

    missing = [key for key in keys if not (values.get(key) or "").strip()]
    if missing:
        raise ConfigError(f"Missing or empty keys in {env_file}: {', '.join(missing)}")
    return {key: (values[key] or "").strip() for key in keys}, env_file


def load_settings() -> Settings:
    gemini, voice = conf().models.providers.google.api_key_env, conf().models.tts.voice_id_env
    values, env_file = _require(gemini, voice)
    return Settings(gemini_api_key=values[gemini], voice_id=values[voice], env_file=env_file)


def load_gemini() -> GeminiSettings:
    key = conf().models.providers.google.api_key_env
    values, env_file = _require(key)
    return GeminiSettings(gemini_api_key=values[key], env_file=env_file)


def load_anthropic() -> AnthropicSettings:
    """models.providers.anthropic.api_key_env (the channel's: its own key variable). The SDK's own name,
    ANTHROPIC_API_KEY, is refused even alongside it: the key must never be one the SDK could
    pick up by itself."""
    key = conf().models.providers.anthropic.api_key_env
    raw, env_file = _read_env()
    if "ANTHROPIC_API_KEY" in raw:
        raise ConfigError(f"ANTHROPIC_API_KEY is set in {env_file}: rename it to {key}")
    values, env_file = _require(key)
    return AnthropicSettings(api_key=values[key], env_file=env_file)


def load_optional(key: str) -> str | None:
    """An optional credential's value from the env file, None when missing or empty."""
    values, _ = _read_env()
    return (values.get(key) or "").strip() or None


def load_meta() -> MetaSettings:
    m = conf().publish.meta
    values, env_file = _require(m.app_id_env, m.app_secret_env, m.page_id_env, m.page_token_env)
    return MetaSettings(
        app_id=values[m.app_id_env],
        app_secret=values[m.app_secret_env],
        page_id=values[m.page_id_env],
        page_token=values[m.page_token_env],
        env_file=env_file,
    )


def load_buffer() -> BufferSettings:
    b = conf().publish.buffer
    values, env_file = _require(
        b.api_key_env, b.org_id_env, b.channel_instagram_env, b.channel_tiktok_env, b.channel_youtube_env
    )
    return BufferSettings(
        api_key=values[b.api_key_env],
        org_id=values[b.org_id_env],
        channel_instagram=values[b.channel_instagram_env],
        channel_tiktok=values[b.channel_tiktok_env],
        channel_youtube=values[b.channel_youtube_env],
        env_file=env_file,
    )


@dataclass(frozen=True)
class TikTokSettings:
    """TikTok's Content Posting API (reelline/publish/tiktok.py): the app's key and secret and the account's refresh token."""

    client_key: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    env_file: Path


def load_tiktok() -> TikTokSettings:
    t = conf().publish.tiktok
    values, env_file = _require(t.client_key_env, t.client_secret_env, t.refresh_token_env)
    return TikTokSettings(
        client_key=values[t.client_key_env],
        client_secret=values[t.client_secret_env],
        refresh_token=values[t.refresh_token_env],
        env_file=env_file,
    )


@dataclass(frozen=True)
class TikTokBusinessSettings:
    """TikTok API for Business, Accounts API (reelline/pipeline/comments.py, channel docs/comments.md): the
    app's id and secret, the account's open_id (business_id) and its refresh token."""

    app_id: str
    app_secret: str = field(repr=False)
    business_id: str
    refresh_token: str = field(repr=False)
    env_file: Path


def load_tiktok_business() -> TikTokBusinessSettings:
    t = conf().publish.tiktok_business
    values, env_file = _require(t.app_id_env, t.app_secret_env, t.business_id_env, t.refresh_token_env)
    return TikTokBusinessSettings(
        app_id=values[t.app_id_env],
        app_secret=values[t.app_secret_env],
        business_id=values[t.business_id_env],
        refresh_token=values[t.refresh_token_env],
        env_file=env_file,
    )


def load_youtube() -> YouTubeSettings:
    """steps.analytics.youtube_api's four keys, each optional (missing or empty: None)."""
    y = conf().steps.analytics.youtube_api
    raw, env_file = _read_env()

    def get(key: str) -> str | None:
        return (raw.get(key) or "").strip() or None

    return YouTubeSettings(
        api_key=get(y.api_key_env),
        client_id=get(y.client_id_env),
        client_secret=get(y.client_secret_env),
        refresh_token=get(y.refresh_token_env),
        env_file=env_file,
    )


def load_telegram() -> TelegramSettings:
    t = conf().review.telegram
    values, env_file = _require(t.bot_token_env, t.chat_id_env)
    return TelegramSettings(bot_token=values[t.bot_token_env], chat_id=values[t.chat_id_env], env_file=env_file)


def load_env_value(key: str) -> str:
    """One required value from the env file (a webhook URL, a secret)."""
    values, _ = _require(key)
    return values[key]


def load_pexels() -> PexelsSettings:
    key = conf().render.pexels.api_key_env
    values, env_file = _require(key)
    return PexelsSettings(api_key=values[key], env_file=env_file)


def load_pixabay() -> PixabaySettings:
    key = conf().render.pixabay.api_key_env
    values, env_file = _require(key)
    return PixabaySettings(api_key=values[key], env_file=env_file)


def load_r2() -> R2Settings:
    r = conf().publish.r2
    values, env_file = _require(
        r.account_id_env, r.access_key_id_env, r.secret_access_key_env, r.endpoint_env, r.bucket_env
    )
    raw, _ = _read_env()
    public = (raw.get(r.public_url_env) or "").strip().rstrip("/") or None
    return R2Settings(
        account_id=values[r.account_id_env],
        access_key_id=values[r.access_key_id_env],
        secret_access_key=values[r.secret_access_key_env],
        endpoint=values[r.endpoint_env].rstrip("/"),
        bucket=values[r.bucket_env],
        env_file=env_file,
        public_url=public,
    )


def load_cloudflare() -> CloudflareSettings:
    """The Pages token (publish.credits_page.api_token_env) and the account (R2's: the same one)."""
    token, account = conf().publish.credits_page.api_token_env, conf().publish.r2.account_id_env
    values, env_file = _require(token, account)
    return CloudflareSettings(api_token=values[token], account_id=values[account], env_file=env_file)


def load_freesound() -> FreesoundSettings:
    key = conf().render.freesound.api_key_env
    values, env_file = _require(key)
    return FreesoundSettings(api_key=values[key], env_file=env_file)
