"""Language packs: everything language-specific the engine reads (month names, number words, fact-check stems).
`current()` is the channel's (channel.language in config.yaml); `get(code)` any pack."""

from __future__ import annotations

from reelline.language.base import Language

__all__ = ["Language", "current", "get"]


def get(code: str) -> Language:
    if code == "ka":
        from reelline.language.ka import KA

        return KA
    if code == "en":
        from reelline.language.en import EN

        return EN
    from reelline.core.errors import ConfigError

    raise ConfigError(f"unknown language {code!r} (channel.language): packs are ka and en")


def current() -> Language:
    from reelline.config import conf

    return get(conf().channel.language)
