"""URL normalization for de-duplication.

normalize_url() gives one spelling per page: lowercase scheme and host, no default
port, no fragment, no tracking parameters (TRACKING_PARAMS, TRACKING_PREFIXES), the
remaining query parameters sorted, no trailing slash, and one percent-encoding
(raw Unicode encoded, escapes uppercased). The scheme and "www." are kept: they can
be different sites.

strip_tracking() only drops the tracking parameters (for a link we show, e.g. a
shortlink's target).

prefer_canonical() takes a page's <link rel="canonical"> over its own URL, unless
the canonical is unusable: not http(s), or a site's front page while the page itself
is deeper (a common CMS mistake that would make every article the same URL).
"""

from __future__ import annotations

import contextlib
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from reelline.config import conf
from reelline.core.errors import ReellineError

# Characters left as they are when (re-)encoding a path or query ("%" keeps existing escapes).
_PATH_SAFE = "/:@!$&'()*+,;=-._~%"
_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")


class UrlError(ReellineError, ValueError):
    """Raised for a URL that isn't an absolute http(s) URL."""


def _encode(part: str, safe: str) -> str:
    return _ESCAPE.sub(lambda m: m.group(0).upper(), quote(part, safe=safe))


def is_tracking_param(name: str) -> bool:
    name = name.lower()
    return name in conf().steps.ingest.tracking_params or name.startswith(conf().steps.ingest.tracking_prefixes)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        raise UrlError(f"Not an absolute http(s) URL: {url!r}")
    host = parts.hostname.lower()
    with contextlib.suppress(UnicodeError):
        host = host.encode("idna").decode("ascii")
    port = parts.port
    netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"

    path = _encode(parts.path, _PATH_SAFE).rstrip("/")
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not is_tracking_param(k)),
        quote_via=quote,
        safe="/:@!$'()*+,;-._~",
    )
    return urlunsplit((scheme, netloc, path, _ESCAPE.sub(lambda m: m.group(0).upper(), query), ""))


def strip_tracking(url: str) -> str:
    """The URL as it is, minus its tracking parameters (TRACKING_PARAMS,
    TRACKING_PREFIXES): the rest of the query keeps its order and spelling."""
    parts = urlsplit(url.strip())
    if not parts.query:
        return url.strip()
    kept = [p for p in parts.query.split("&") if p and not is_tracking_param(p.split("=", 1)[0])]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))


def prefer_canonical(page_url: str, canonical_href: str | None) -> str:
    """The normalized canonical URL if it's usable, else the normalized page URL."""
    page = normalize_url(page_url)
    if not canonical_href or not canonical_href.strip():
        return page
    try:
        canonical = normalize_url(urljoin(page_url, canonical_href.strip()))
    except UrlError:
        return page
    if urlsplit(canonical).path in ("", "/") and urlsplit(page).path not in ("", "/"):
        return page
    return canonical


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
