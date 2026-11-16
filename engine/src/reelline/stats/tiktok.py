"""TikTok's own numbers, TikTok API for Business (Accounts API v1.3): GET /business/video/list/
with the Access-Token header, filtered to the posts' video ids: views, likes, comments, shares,
reach, average / total watch time and the full-video watch rate. Buffer gives TikTok views only;
this adds the rest where the app has the video-list scope. The access token comes from the same
TIKTOK_BUSINESS_* credentials as the comment adapter (reelline/pipeline/comments.py).

Built from the docs and tested on a mock transport only: untested against the live API. Off unless
steps.analytics.collectors names "tiktok". A 429 / 5xx is retried (`retries`, `sleep`); an expired
token (code 40102) is refreshed once; any other error code is a StatsError (no secrets in it).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx

from reelline.config import conf
from reelline.stats.base import BaseCollector, Ctx, SourcePart, StatPub, StatsError, as_int, empty

API = "https://business-api.tiktok.com/open_api/v1.3"
FIELDS = [
    "item_id",
    "video_views",
    "likes",
    "comments",
    "shares",
    "reach",
    "average_time_watched",
    "total_time_watched",
    "full_video_watched_rate",
]
EXPIRED = {40102, 40105}


def parse_tiktok(item: dict) -> dict:
    out = empty()
    out.update(
        views=as_int(item.get("video_views")),
        likes=as_int(item.get("likes")),
        comments=as_int(item.get("comments")),
        shares=as_int(item.get("shares")),
        reach=as_int(item.get("reach")) or None,
    )  # 0 is "not given", as through Buffer
    if item.get("average_time_watched") is not None:
        out["avg_watch_s"] = float(item["average_time_watched"])
    if item.get("total_time_watched") is not None:
        out["total_watch_s"] = float(item["total_time_watched"])
    if item.get("full_video_watched_rate") is not None:
        out["avg_pct"] = round(float(item["full_video_watched_rate"]) * 100, 1)
    return out


class TikTokClient:
    def __init__(
        self,
        settings=None,
        http: httpx.Client | None = None,
        base: str = API,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if settings is None:
            from reelline.config.credentials import load_tiktok_business

            settings = load_tiktok_business()
        self.s, self.base, self.retries, self.sleep = settings, base, retries, sleep
        self.http = http or httpx.Client(timeout=conf().steps.analytics.http_timeout_s)
        self._token: str | None = None

    def _redact(self, text: str) -> str:
        for secret in (self.s.app_secret, self.s.refresh_token, self._token):
            if secret:
                text = text.replace(secret, "<secret>")
        return text

    def _call(
        self,
        action: str,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
        token: bool = True,
        _refreshed: bool = False,
    ) -> dict:
        headers = {"Access-Token": self.access_token()} if token else {}
        for attempt in range(self.retries + 1):
            try:
                r = self.http.request(method, f"{self.base}{path}", params=params, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt < self.retries:
                    self.sleep(2**attempt)
                    continue
                raise StatsError(self._redact(f"TikTok {action}: {type(exc).__name__}")) from None
            if (r.status_code == 429 or r.status_code >= 500) and attempt < self.retries:
                self.sleep(2**attempt)
                continue
            break
        try:
            data = r.json()
        except ValueError:
            data = {}
        code = data.get("code", 0 if r.status_code < 400 else r.status_code)
        if code in EXPIRED and token and not _refreshed:
            self._token = None
            return self._call(action, method, path, params=params, body=body, token=token, _refreshed=True)
        if code != 0:
            raise StatsError(
                self._redact(f"TikTok {action}: {data.get('message') or 'error'} (code {code}, HTTP {r.status_code})")[
                    :500
                ]
            )
        return data.get("data") or {}

    def access_token(self) -> str:
        if self._token is None:
            data = self._call(
                "stats_tt_token",
                "POST",
                "/tt_user/oauth2/refresh_token/",
                token=False,
                body={
                    "client_id": self.s.app_id,
                    "client_secret": self.s.app_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self.s.refresh_token,
                },
            )
            self._token = data["access_token"]
        return self._token

    def videos(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 20):
            data = self._call(
                "stats_tt_videos",
                "GET",
                "/business/video/list/",
                params={
                    "business_id": self.s.business_id,
                    "fields": json.dumps(FIELDS),
                    "filters": json.dumps({"video_ids": ids[i : i + 20]}),
                },
            )
            out += data.get("videos") or []
        return out


class TikTokStats(BaseCollector):
    name = "tiktok"
    sources: tuple[str, ...] = ("tiktok_business",)
    channels = {"tiktok_business": ("tiktok",)}

    def __init__(self, client: TikTokClient | None):
        self.client = client

    def missing(self) -> dict[str, str]:
        return {} if self.client is not None else {"tiktok_business": "TIKTOK_BUSINESS_* keys in .env"}

    def eligible(self, source: str, pub: StatPub, ctx: Ctx) -> bool:
        return bool(pub.native_id)

    def collect(self, source: str, pubs: list[StatPub], ctx: Ctx):
        items = {str(x.get("item_id")): x for x in self.client.videos([p.native_id for p in pubs])}
        for p in pubs:
            if p.native_id in items:
                yield p, SourcePart(parse_tiktok(items[p.native_id]), {"tiktok_business": items[p.native_id]})
