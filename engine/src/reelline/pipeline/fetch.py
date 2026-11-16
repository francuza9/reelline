"""Polite HTTP for ingest: one requests session, our User-Agent, robots.txt, a minimum
gap per host, conditional GET, timeouts, a small retry count and a size cap.

Fetcher.allowed(url) reads the host's robots.txt once per Fetcher (RFC 9309): a 2xx
is parsed for our agent (channel.robots_agent) with Protego, a 4xx means no rules
(all allowed), a 5xx or a network error raises FetchError (the source's run fails;
nothing is assumed). Protego, not urllib.robotparser: the stdlib parser has no "*"
or "$" wildcards and cuts a rule at "?" ("Disallow: /r?" becomes "/r", which blocks
Hacker News' /rss). A Crawl-delay above the minimum gap is honored up to
max_crawl_delay_s.

Fetcher.get(url, etag=..., last_modified=...) sends If-None-Match / If-Modified-Since
when given; a 304 comes back as Response(not_modified=True). Any other non-200 after
the retries raises FetchError with the status.

Bodies are kept as bytes: feedparser, lxml and trafilatura each detect the encoding
themselves (from the XML declaration or <meta charset>), which is more reliable than
requests' guess for pages that don't declare one in Content-Type.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests
from protego import Protego

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.media import __version__

USER_AGENT = conf().channel.user_agent.format(version=__version__)


class FetchError(ReellineError):
    """A request that failed after its retries; status is the HTTP status, if any."""

    def __init__(self, url: str, message: str, status: int | None = None):
        super().__init__(f"GET {url}: {message}")
        self.url = url
        self.status = status


@dataclass(frozen=True)
class Response:
    url: str  # after redirects
    status: int
    content: bytes = b""
    content_type: str = ""
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


@dataclass
class _Robots:
    parser: Protego | None  # None: no rules, everything allowed
    crawl_delay: float = 0.0


@dataclass
class Fetcher:
    session: requests.Session = field(default_factory=requests.Session)
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = lambda message: print(message, flush=True)
    _last: dict[str, float] = field(default_factory=dict)
    _robots: dict[str, _Robots] = field(default_factory=dict)

    def __post_init__(self):
        self.session.headers["User-Agent"] = USER_AGENT

    # ---- politeness ---------------------------------------------------------------

    def _wait_turn(self, host: str) -> None:
        gap = conf().steps.ingest.min_interval_s
        robots = self._robots.get(host)
        if robots is not None:
            gap = max(gap, min(robots.crawl_delay, conf().steps.ingest.max_crawl_delay_s))
        last = self._last.get(host)
        if last is not None:
            wait = last + gap - self.clock()
            if wait > 0:
                self.sleep(wait)

    def _request(
        self, url: str, headers: dict[str, str], max_bytes: int, truncate: bool = False
    ) -> tuple[requests.Response, bytes]:
        host = (urlsplit(url).hostname or "").lower()
        timeout = (conf().steps.ingest.connect_timeout_s, conf().steps.ingest.read_timeout_s)
        retries = conf().steps.ingest.retries
        for attempt in range(retries + 1):
            self._wait_turn(host)
            try:
                response = self.session.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
                try:
                    body = self._read_capped(url, response, max_bytes, truncate)
                finally:
                    response.close()
            except requests.RequestException as exc:
                if attempt < retries:
                    self.log(f"    {host}: {type(exc).__name__}, retry {attempt + 1}/{retries}")
                    self.sleep(conf().steps.ingest.retry_wait_s * (attempt + 1))
                    continue
                raise FetchError(url, f"{type(exc).__name__}: {exc}") from None
            finally:
                self._last[host] = self.clock()
            if response.status_code in conf().steps.ingest.retry_statuses and attempt < retries:
                retry_after = _retry_after(response.headers.get("Retry-After"))
                if retry_after is not None and retry_after > conf().steps.ingest.max_retry_after_s:
                    raise FetchError(
                        url,
                        f"HTTP {response.status_code}, Retry-After {retry_after:g}s is too long",
                        response.status_code,
                    )
                wait = retry_after if retry_after is not None else conf().steps.ingest.retry_wait_s * (attempt + 1)
                self.log(f"    {host}: HTTP {response.status_code}, retry {attempt + 1}/{retries} in {wait:g}s")
                self.sleep(wait)
                continue
            return response, body
        raise AssertionError("unreachable")

    @staticmethod
    def _read_capped(url: str, response: requests.Response, max_bytes: int, truncate: bool) -> bytes:
        """The body, failing past max_bytes (or cut at max_bytes with truncate)."""
        declared = response.headers.get("Content-Length")
        if not truncate and declared and declared.isdigit() and int(declared) > max_bytes:
            raise FetchError(
                url, f"response is {int(declared)} bytes, over the {max_bytes} limit", response.status_code
            )
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > max_bytes:
                if truncate:
                    chunks.append(chunk[: max_bytes - size + len(chunk)])
                    break
                raise FetchError(url, f"response is over the {max_bytes} byte limit", response.status_code)
            chunks.append(chunk)
        return b"".join(chunks)

    # ---- robots.txt ---------------------------------------------------------------

    def _robots_for(self, url: str, extra_headers: dict[str, str] | None = None) -> _Robots:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host in self._robots:
            return self._robots[host]
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            response, body = self._request(
                robots_url, dict(extra_headers or {}), conf().steps.ingest.robots_max_bytes, truncate=True
            )
        except FetchError as exc:
            raise FetchError(url, f"robots.txt unreachable ({exc})", exc.status) from None
        status = response.status_code
        if 400 <= status < 500:
            robots = _Robots(None)
        elif 200 <= status < 300:
            parser = Protego.parse(body.decode("utf-8", errors="replace"))
            delay = parser.crawl_delay(conf().channel.robots_agent)
            robots = _Robots(parser, float(delay) if delay else 0.0)
        else:
            raise FetchError(url, f"robots.txt unreachable (HTTP {status})", status)
        self._robots[host] = robots
        return robots

    def allowed(self, url: str, extra_headers: dict[str, str] | None = None) -> bool:
        """extra_headers go with the robots.txt request (an API that errors on every
        request without Accept-Language errors on robots.txt too)."""
        robots = self._robots_for(url, extra_headers)
        return robots.parser is None or robots.parser.can_fetch(url, conf().channel.robots_agent)

    # ---- GET ----------------------------------------------------------------------

    def get(
        self,
        url: str,
        etag: str | None = None,
        last_modified: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Response:
        headers = dict(extra_headers or {})
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        response, body = self._request(url, headers, conf().steps.ingest.max_bytes)
        if response.status_code == 304:
            return Response(url=response.url, status=304, etag=etag, last_modified=last_modified, not_modified=True)
        if response.status_code != 200:
            raise FetchError(url, f"HTTP {response.status_code}", response.status_code)
        return Response(
            url=response.url,
            status=200,
            content=body,
            content_type=response.headers.get("Content-Type", ""),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # an HTTP date: fall back to our own wait
