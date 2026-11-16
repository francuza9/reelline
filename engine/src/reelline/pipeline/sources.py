"""The sources registry: sources.yaml, one entry per news source.

load_sources() reads and validates the whole file and fails loudly (SourceRegistryError)
on anything off: a missing or unknown key, a duplicate id, a value outside its set,
a listing without a selector (or a selector that doesn't compile), an rss entry with
one, or an em/en dash in any text. The registry is static config; runtime state per
source (failures, disabled, validators) lives in the database (reelline/pipeline/db.py).

Entry keys:
  id          snake_case, unique; the database's sources.id
  name        human name
  topic       education | startups | tech (the stories' topic_hint)
  geography   georgia | global
  lang        ka | en (the language the source publishes in)
  type        rss (a feed URL) | listing (an HTML list page + a CSS selector) |
              json (a public JSON endpoint the site's own frontend calls)
  url         the feed URL, the listing page URL or the JSON endpoint
  json        json only, a mapping:
                items         dotted path to the list of items ("" if the root is the list)
                url_template  the public article URL from item fields: "https://x.ge/a/{id}"
                              ({a.b} for nested fields), or
                url           the item field holding the article URL (one of the two)
                title         the item field with the headline
                date          optional: the field with the date (ISO text, epoch seconds or
                              milliseconds, or a Georgian date)
                text          optional: the field with the body (HTML or text); with it the
                              article page is never fetched, without it it is
  headers     optional: extra request headers for the source URL (and its host's
              robots.txt), only Accept-Language and Accept (an API that needs a
              language)
  selector    listing only: CSS selector for the article links; each match is an
              <a href> or an element holding one (its first a[href] is used)
  text_from   optional: article (default; the article page, via trafilatura) |
              feed (the feed entry's own content; rss only)
  page_dates  optional, default true: false ignores the date the extractor
              (htmldate) finds on the article page (for sites whose pages carry no
              real publish date, or a wrong one); the dates below still count
  date_selector       optional, listing only: CSS selector for the date element
              inside each listing match; its text is read by reelline/pipeline/dates.py
              (Georgian month names, day-first numbers)
  page_date_selector  optional: CSS selector for the date element on the article
              page, read the same way (article text only)
  min_words   optional: this source's word floor instead of steps.ingest.min_words
  assess      optional, default true: false archives the source: its stories are stored
              as usual (same politeness and health rules) with status "archived", and
              are never assessed, never sent to any model and never picked
              (channel docs/topics.md, the tech news archive)
  priority    1 (most important) .. 3
  enabled     true | false (false needs notes saying why)
  notes       free text: how it was verified, why it's disabled
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from cssselect import SelectorError
from lxml.cssselect import CSSSelector

from reelline.config import conf
from reelline.core.errors import ReellineError

TOPICS = ("education", "startups", "tech")
GEOGRAPHIES = ("georgia", "global")
LANGS = ("ka", "en")
TYPES = ("rss", "listing", "json")
JSON_KEYS = ("items", "url_template", "url", "title", "date", "text")
HEADERS = ("Accept-Language", "Accept")
TEXT_FROM = ("article", "feed")
PRIORITIES = (1, 2, 3)

_REQUIRED = ("id", "name", "topic", "geography", "lang", "type", "url", "priority", "enabled", "notes")
_OPTIONAL = (
    "selector",
    "text_from",
    "page_dates",
    "date_selector",
    "page_date_selector",
    "min_words",
    "json",
    "headers",
    "assess",
)
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_DASHES = ("\u2014", "\u2013")  # em dash, en dash: hyphens only


class SourceRegistryError(ReellineError, ValueError):
    """Raised when sources.yaml is missing or invalid."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    topic: str
    geography: str
    lang: str
    type: str
    url: str
    priority: int
    enabled: bool
    notes: str
    selector: str | None = None
    text_from: str = "article"
    page_dates: bool = True
    date_selector: str | None = None
    page_date_selector: str | None = None
    min_words: int | None = None
    json: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    assess: bool = True

    @property
    def item_text(self) -> bool:
        """The body comes with the item (feed entry, JSON field); no article fetch."""
        return self.text_from == "feed" or bool(self.json and self.json.get("text"))


def _entry(raw: object, index: int) -> Source:
    where = f"entry {index + 1}"
    if not isinstance(raw, dict):
        raise SourceRegistryError(f"{where}: expected a mapping, got {type(raw).__name__}")
    if isinstance(raw.get("id"), str):
        where = f"entry {index + 1} ({raw['id']})"

    missing = [k for k in _REQUIRED if k not in raw]
    unknown = sorted(set(raw) - set(_REQUIRED) - set(_OPTIONAL))
    if missing:
        raise SourceRegistryError(f"{where}: missing keys: {', '.join(missing)}")
    if unknown:
        raise SourceRegistryError(f"{where}: unknown keys: {', '.join(unknown)}")

    def text(key: str, allow_empty: bool = False) -> str:
        value = raw[key]
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            raise SourceRegistryError(f"{where}: {key} must be a non-empty string")
        if any(d in value for d in _DASHES):
            raise SourceRegistryError(f"{where}: {key} has an em/en dash; use a hyphen (-)")
        return value.strip()

    def css(key: str) -> str:
        value = text(key)
        try:
            CSSSelector(value)
        except SelectorError as exc:
            raise SourceRegistryError(f"{where}: {key} {value!r} doesn't compile: {exc}") from None
        return value

    def one_of(key: str, allowed: tuple) -> str:
        value = raw[key]
        if value not in allowed:
            raise SourceRegistryError(f"{where}: {key} must be one of {', '.join(map(str, allowed))}, got {value!r}")
        return value

    sid = text("id")
    if not _ID.match(sid):
        raise SourceRegistryError(f"{where}: id must be snake_case ([a-z][a-z0-9_]*)")
    url = text("url")
    if not re.match(r"^https?://[^/\s]+", url):
        raise SourceRegistryError(f"{where}: url must be an http(s) URL, got {url!r}")
    if not isinstance(raw["enabled"], bool):
        raise SourceRegistryError(f"{where}: enabled must be true or false")
    if isinstance(raw["priority"], bool) or raw["priority"] not in PRIORITIES:
        raise SourceRegistryError(f"{where}: priority must be 1, 2 or 3, got {raw['priority']!r}")

    kind = one_of("type", TYPES)
    selector = None
    if kind == "listing":
        if "selector" not in raw:
            raise SourceRegistryError(f"{where}: a listing source needs a selector")
        selector = css("selector")
    else:
        for key in ("selector", "date_selector"):
            if key in raw:
                raise SourceRegistryError(f"{where}: {key} is for listing sources only")
    date_selector = css("date_selector") if "date_selector" in raw else None

    text_from = raw.get("text_from", "article")
    if text_from not in TEXT_FROM:
        raise SourceRegistryError(f"{where}: text_from must be one of {', '.join(TEXT_FROM)}, got {text_from!r}")
    if text_from == "feed" and kind != "rss":
        raise SourceRegistryError(f"{where}: text_from: feed needs type: rss")
    json_spec = None
    if kind == "json":
        json_spec = raw.get("json")
        if not isinstance(json_spec, dict):
            raise SourceRegistryError(f"{where}: a json source needs a json mapping (items, url_template, title, ...)")
        bad = sorted(set(json_spec) - set(JSON_KEYS))
        if bad:
            raise SourceRegistryError(f"{where}: unknown json keys: {', '.join(bad)}")
        if not all(isinstance(v, str) for v in json_spec.values()):
            raise SourceRegistryError(f"{where}: json values must be strings")
        if ("url_template" in json_spec) == ("url" in json_spec):
            raise SourceRegistryError(f"{where}: json needs exactly one of url_template, url")
        for key in ("items", "title"):
            if key not in json_spec:
                raise SourceRegistryError(f"{where}: json needs {key}")
        if "text_from" in raw:
            raise SourceRegistryError(f"{where}: json sources take their text from json.text, not text_from")
    elif "json" in raw:
        raise SourceRegistryError(f"{where}: json is for json sources only")

    headers = raw.get("headers")
    if headers is not None:
        if not isinstance(headers, dict) or not all(isinstance(v, str) and v for v in headers.values()):
            raise SourceRegistryError(f"{where}: headers must map header names to values")
        bad = sorted(set(headers) - set(HEADERS))
        if bad:
            raise SourceRegistryError(f"{where}: headers may only set {', '.join(HEADERS)}, not {', '.join(bad)}")

    page_date_selector = css("page_date_selector") if "page_date_selector" in raw else None
    if page_date_selector and (text_from == "feed" or (json_spec and json_spec.get("text"))):
        raise SourceRegistryError(f"{where}: page_date_selector needs article pages (not text_from: feed)")

    source_min_words = raw.get("min_words")
    if source_min_words is not None and (
        isinstance(source_min_words, bool) or not isinstance(source_min_words, int) or source_min_words < 1
    ):
        raise SourceRegistryError(f"{where}: min_words must be a whole number of at least 1")

    page_dates = raw.get("page_dates", True)
    if not isinstance(page_dates, bool):
        raise SourceRegistryError(f"{where}: page_dates must be true or false")

    assess = raw.get("assess", True)
    if not isinstance(assess, bool):
        raise SourceRegistryError(f"{where}: assess must be true or false")

    notes = text("notes", allow_empty=True)
    if raw["enabled"] is False and not notes:
        raise SourceRegistryError(f"{where}: a disabled source needs notes saying why")

    return Source(
        id=sid,
        name=text("name"),
        topic=one_of("topic", TOPICS),
        geography=one_of("geography", GEOGRAPHIES),
        lang=one_of("lang", LANGS),
        type=kind,
        url=url,
        priority=raw["priority"],
        enabled=raw["enabled"],
        notes=notes,
        selector=selector,
        text_from=text_from,
        page_dates=page_dates,
        date_selector=date_selector,
        page_date_selector=page_date_selector,
        min_words=source_min_words,
        json=dict(json_spec) if json_spec else None,
        headers=dict(headers) if headers else None,
        assess=assess,
    )


def load_sources(path: Path | None = None) -> list[Source]:
    """Every entry in the registry (default: the config's sources), in file order, validated."""
    path = Path(path or conf().sources)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceRegistryError(f"Can't read {path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise SourceRegistryError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(raw, list) or not raw:
        raise SourceRegistryError(f"{path}: expected a non-empty list of sources")
    sources = [_entry(entry, i) for i, entry in enumerate(raw)]
    seen: set[str] = set()
    for s in sources:
        if s.id in seen:
            raise SourceRegistryError(f"{path}: duplicate id {s.id!r}")
        seen.add(s.id)
    return sources


def by_id(sources: list[Source]) -> dict[str, Source]:
    return {s.id: s for s in sources}
