"""The platform clients, the API call log and their construction."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from reelline.pipeline import db
from reelline.publish.base import Publisher
from reelline.publish.registry import get_publishers
from reelline.storage.blob import BlobStore
from reelline.storage.registry import get_blob_store


class CallLog:
    """The `record` callback of the API clients: one publish_calls row per call."""

    def __init__(self, conn: sqlite3.Connection, reel_id: int | None, prefix: str = ""):
        self.conn, self.reel_id, self.prefix = conn, reel_id, prefix

    def __call__(
        self,
        *,
        action: str,
        method: str,
        endpoint: str,
        request=None,
        status: int | None = None,
        external_id: str | None = None,
        error: str | None = None,
        channel: str | None = None,
    ) -> None:
        text = None if request is None else json.dumps(request, ensure_ascii=False, default=str)[:4000]
        with db.transaction(self.conn):
            self.conn.execute(
                """INSERT INTO publish_calls (reel_id, channel, action, method, endpoint, request, status, external_id,
                       error, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.reel_id,
                    channel,
                    self.prefix + action,
                    method,
                    endpoint,
                    text,
                    status,
                    external_id,
                    error[:1000] if error else None,
                    db.now_iso(),
                ),
            )


@dataclass
class Clients:
    blob: BlobStore
    publishers: dict[str, Publisher]  # channel -> the adapter that publishes it (publish.targets)
    # (conn, reel_id) -> {"credits": URL or None, "links": [URL, ...]} ({} with nothing to
    # deploy): reelline/pipeline/credits.py publish_site(); raises CreditsError.
    site: Callable[[sqlite3.Connection, int], dict] | None = None
    # channel -> its commenter (reelline/pipeline/comments.py CommentClients).
    comments: Callable[[str], object] | None = None


class _LazyFacebook:
    """A FacebookClient made on first use: a channel with no Meta publisher and no Facebook/Instagram comments
    never needs the Meta credentials."""

    def __init__(self, record):
        self._record, self._client = record, None

    def __getattr__(self, name):
        if self._client is None:
            from reelline.publish.meta import FacebookClient

            self._client = FacebookClient(record=self._record)
        return getattr(self._client, name)


def real_clients(conn: sqlite3.Connection, reel_id: int | None, prefix: str = "") -> Clients:
    from reelline.pipeline.comments import CommentClients
    from reelline.pipeline.credits import publish_site
    from reelline.pipeline.stats import limits_sink

    record = CallLog(conn, reel_id, prefix)
    # Every Buffer reply's RateLimit is kept, so stats leave publishing its budget.
    publishers = get_publishers(record=record, on_limits=limits_sink(conn))
    facebook = next((p.client for p in publishers.values() if p.name == "meta"), None)
    if facebook is None:  # the comments still go through the Graph API: its credentials load when first used
        facebook = _LazyFacebook(record)
    return Clients(
        get_blob_store(record=record),
        publishers,
        site=lambda c, rid: publish_site(c, rid),
        comments=CommentClients(facebook, record),
    )


def platform_client(clients: Clients, channel: str):
    """The adapter's own client (BufferClient, FacebookClient): for the platform-specific live test and
    verify tools below, which probe a platform's own API."""
    return clients.publishers[channel].client
