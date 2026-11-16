"""publish.targets (channel -> adapter) -> the Publisher: get_publisher(channel), get_publishers().

`given` mirrors reelline/notify/registry.py: a Publisher is used as it is; anything else (a
BufferClient, a FacebookClient, a test's fake of one) becomes the connection of the adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from reelline.config import conf
from reelline.publish.base import Publisher

KINDS = ("buffer", "meta", "youtube", "tiktok", "local")


def _adapter(kind: str) -> Callable[..., Publisher]:
    if kind == "buffer":
        from reelline.publish.buffer import BufferPublisher

        return lambda given, **kw: BufferPublisher(client=given, **kw)
    if kind == "meta":
        from reelline.publish.meta import MetaPublisher

        return lambda given, **kw: MetaPublisher(client=given, **kw)
    if kind == "youtube":
        from reelline.publish.youtube import YouTubePublisher

        return lambda given, **kw: YouTubePublisher(client=given, **kw)
    if kind == "tiktok":
        from reelline.publish.tiktok import TikTokPublisher

        return lambda given, **kw: TikTokPublisher(client=given, **kw)
    if kind == "local":
        from reelline.publish.local import LocalPublisher

        return lambda given, **kw: LocalPublisher(**kw)
    raise ValueError(f"no publisher {kind!r} (publish.targets values: {', '.join(KINDS)})")


def target_of(channel: str) -> str:
    try:
        return conf().publish.targets[channel]
    except KeyError:
        raise ValueError(f"no publish target for channel {channel!r} (publish.targets)") from None


def get_publisher(channel: str, given: Any = None, **kw: Any) -> Publisher:
    if isinstance(given, Publisher):
        return given
    return _adapter(target_of(channel))(given, **kw)


def get_publishers(
    channels: list[str] | None = None, *, given: dict[str, Any] | None = None, **kw: Any
) -> dict[str, Publisher]:
    """One Publisher per channel; channels that share an adapter share its instance."""
    out: dict[str, Publisher] = {}
    by_kind: dict[str, Publisher] = {}
    for ch in channels or list(conf().publish.targets):
        kind = target_of(ch)
        if given and ch in given:
            out[ch] = get_publisher(ch, given[ch], **kw)
        else:
            if kind not in by_kind:
                by_kind[kind] = get_publisher(ch, **kw)
            out[ch] = by_kind[kind]
    return out
