"""Parsing feeds, listing pages and JSON endpoints into items, and the dates in them."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from urllib.parse import urljoin

import feedparser
import lxml.etree
import lxml.html
from lxml.cssselect import CSSSelector

from reelline.config import conf
from reelline.pipeline.dates import parse_date_text
from reelline.pipeline.ingest.base import Article, Item, SourceError
from reelline.pipeline.sources import Source
from reelline.pipeline.urls import UrlError, normalize_url


def _struct_to_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime(*value[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def parse_feed(content: bytes, base_url: str) -> list[Item]:
    parsed = feedparser.parse(content, response_headers={"content-location": base_url})
    if not parsed.entries:
        if parsed.bozo:
            raise SourceError(f"not a feed ({type(parsed.bozo_exception).__name__}: {parsed.bozo_exception})")
        if not parsed.version:
            raise SourceError("not a feed (no RSS/Atom root)")
    items = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        content_html = None
        if entry.get("content"):
            content_html = entry["content"][0].get("value")
        content_html = content_html or entry.get("summary")
        items.append(
            Item(
                url=urljoin(base_url, link),
                # html.unescape: some feeds escape twice ("it&#8217;s" survives feedparser).
                title=" ".join(html.unescape(entry.get("title") or "").split()),
                published=_struct_to_dt(entry.get("published_parsed") or entry.get("updated_parsed")),
                content=content_html,
            )
        )
    return items


def first_text(el, selector: CSSSelector | None) -> str | None:
    """The text of the first element under `el` matching `selector`; None without a
    selector, "" when it matches nothing."""
    if selector is None:
        return None
    found = selector(el)
    return " ".join(found[0].text_content().split()) if found else ""


def parse_listing(
    content: bytes, base_url: str, selector: str, date_selector: str | None = None, now: datetime | None = None
) -> list[Item]:
    """Article links from an HTML list page: each selector match is an <a href> or an
    element holding one (its first a[href]). First occurrence of each URL wins; a
    link back to the listing page is skipped. With a date_selector, each card's date
    is read from the first element matching it inside the match (reelline/pipeline/dates.py)."""
    try:
        doc = lxml.html.document_fromstring(content)
    except (lxml.etree.ParserError, ValueError) as exc:
        raise SourceError(f"listing page isn't HTML ({exc})") from None
    # The selector sees the raw HTML (hrefs as written); links are resolved after.
    page_key = normalize_url(base_url)
    base = next((b.get("href") for b in doc.iter("base") if b.get("href")), None)
    if base:
        base_url = urljoin(base_url, base.strip())
    matches = CSSSelector(selector)(doc)
    date_css = CSSSelector(date_selector) if date_selector else None
    if not matches:
        raise SourceError(f"selector {selector!r} matched nothing")
    # The listing page itself (a "blog home" link the selector also catches) is no article.
    items, seen = [], {page_key}
    for el in matches:
        link = el if el.tag == "a" and el.get("href") else next(iter(el.cssselect("a[href]")), None)
        if link is None:
            continue
        try:
            href = urljoin(base_url, (link.get("href") or "").strip())
        except ValueError:
            continue
        if not href.startswith(("http://", "https://")):
            continue
        try:
            key = normalize_url(href)
        except UrlError:
            continue
        if key in seen:
            continue
        seen.add(key)
        title = " ".join(html.unescape(link.text_content() or link.get("title") or "").split())
        date_text = first_text(el, date_css)
        items.append(
            Item(
                url=href,
                title=title,
                date_text=date_text,
                published=parse_date_text(date_text, now) if date_text else None,
            )
        )
    if not items:
        raise SourceError(f"selector {selector!r} matched {len(matches)} elements but no article links")
    return items


def _field(obj, path: str):
    """A dotted path into nested dicts ("" is the object itself); None if missing."""
    for part in path.split(".") if path else ():
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def parse_json_date(value, now: datetime | None = None) -> datetime | None:
    """A JSON date: epoch seconds or milliseconds (by size), ISO 8601 text, or a
    Georgian date (reelline/pipeline/dates.py)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{9,13}", value.strip())):
        seconds = float(value) / (1000 if float(value) > 1e11 else 1)
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        # "2026-09-21T05:58:55.000000Z": fromisoformat takes the Z and 6-digit fractions
        return parse_date(value) or parse_date_text(value, now)
    return None


def parse_json(content: bytes, spec: dict[str, str], now: datetime | None = None) -> list[Item]:
    """Items from a JSON endpoint, per the source's json mapping (reelline/pipeline/sources.py)."""
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise SourceError(f"not JSON ({exc})") from None
    rows = _field(data, spec["items"])
    if not isinstance(rows, list):
        raise SourceError(f"json items path {spec['items']!r} isn't a list")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "url" in spec:
            url = _field(row, spec["url"])
        else:
            try:
                url = re.sub(r"\{([\w.]+)\}", lambda m, row=row: _required(row, m.group(1)), spec["url_template"])
            except KeyError:
                continue
        title = _field(row, spec["title"])
        text = _field(row, spec["text"]) if spec.get("text") else None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        items.append(
            Item(
                url=url,
                title=" ".join(str(title or "").split()),
                published=parse_json_date(_field(row, spec["date"]), now) if spec.get("date") else None,
                content=str(text) if text is not None else ("" if spec.get("text") else None),
            )
        )
    if rows and not items:
        raise SourceError(f"json: {len(rows)} items but none with a usable URL")
    return items


def _required(row: dict, path: str) -> str:
    value = _field(row, path)
    if value is None or value == "":
        raise KeyError(path)
    return str(value)


def parse_date(value: str | None) -> datetime | None:
    """An ISO 8601 date or timestamp as an aware UTC datetime (naive ones are taken as UTC)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_items(source: Source, page, now: datetime | None = None) -> list[Item]:
    """The items of a fetched feed or listing page."""
    if source.type == "rss":
        return parse_feed(page.content, page.url)
    if source.type == "json":
        return parse_json(page.content, source.json, now)
    return parse_listing(page.content, page.url, source.selector, source.date_selector, now)


def item_date(source: Source, item: Item, article: Article | None = None) -> tuple[datetime | None, str | None]:
    """An item's date and where it came from: the feed entry's or the listing card's,
    then the article page's date element (page_date_selector), then the extractor's
    date (only with page_dates). (None, None) when there's none."""
    if item.published is not None:
        return item.published, {"rss": "feed", "listing": "listing", "json": "json"}[source.type]
    if article is not None:
        if article.page_date is not None:
            return article.page_date, "page"
        if source.page_dates and article.published is not None:
            return article.published, "extractor"
    return None, None


def min_words(source: Source) -> int:
    return source.min_words if source.min_words is not None else conf().steps.ingest.min_words
