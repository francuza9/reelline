"""Ingest's shared constants, errors and records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from reelline.core.errors import ReellineError

# htmldate (inside trafilatura): the full timestamp when the page has one.
DATE_PARAMS = {"outputformat": "%Y-%m-%dT%H:%M:%S%z", "extensive_search": True, "original_date": True}
HTML_TYPES = ("text/html", "application/xhtml+xml")
BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "tr",
    "section",
    "article",
    "figure",
    "figcaption",
    "pre",
}


class IngestError(ReellineError, RuntimeError):
    """Bad CLI use or state (unknown source id, a disabled source asked for, ...)."""


class SourceError(ReellineError):
    """A feed or listing page that can't be parsed into items."""


@dataclass(frozen=True)
class Item:
    """One entry from a feed or a listing page."""

    url: str
    title: str
    published: datetime | None = None
    content: str | None = None  # the feed entry's HTML (content, else summary)
    date_text: str | None = None  # a listing card's raw date text (date_selector), "" if none was found


@dataclass(frozen=True)
class Article:
    title: str | None
    text: str
    published: datetime | None  # the extractor's (htmldate's) date
    canonical_href: str | None
    page_date: datetime | None = None  # from page_date_selector
    page_date_text: str | None = None  # its raw text, "" if the selector found nothing
