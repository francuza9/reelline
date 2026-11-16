"""Ingest: collect new stories from the sources registry into the state database.

Usage:
  python -m reelline.pipeline.ingest [--source ID ...] [--dry-run] [--since-days N]
      Every enabled source (sources.yaml), priority 1 first, or just the
      --source ones. --dry-run fetches and extracts but writes nothing (the database
      is opened read-only). --since-days N keeps only items from the last N days,
      for every source in this run (without it, a window applies only to a source's
      first run: steps.ingest.first_run_days). Exit code: 0 unless every source it
      tried failed (1) or the database failed (2); per-source failures stay in the
      run log (runs table) and `report`. --strict exits 1 if any source failed.
  python -m reelline.pipeline.ingest --reset ID [--reset ID ...]
      Re-enables a source the database disabled (robots.txt, or max_failures failed
      runs in a row) and clears its failure count.
  python -m reelline.pipeline.ingest sources [--check]
      The registry. --check fetches every source live (enabled or not, nothing is
      written): status, feed/listing found, items seen, and one sample article's
      word count. Exits 1 if an enabled source fails.
  python -m reelline.pipeline.ingest report
      Per source: stories in the last steps.ingest.report_days days and in total, last
      OK time, failures, disabled reason; then stories by status and topic.

Source types: rss (feedparser), listing (an HTML page + CSS selector) and json (a
public JSON endpoint, fields mapped in sources.yaml). Per source and run: robots.txt
must allow the source URL (if not, the source is disabled in the database with the
reason); it is fetched with the stored ETag / Last-Modified (a 304 is a success with
nothing new); each item's URL is normalized (reelline/pipeline/urls.py) and skipped if known
(a story, or a URL dropped before); unless the item carries its own text (a feed's
text_from: feed, a json source's json.text), the article is fetched (robots.txt
checked per URL) and extracted with trafilatura, its <link rel="canonical">
preferred. Dates: the feed's, the listing card's or the JSON field's, else the page's
date element, else the extractor's (page_dates), else the first-seen time
(reelline/pipeline/dates.py reads Georgian dates). Then an item is dropped (and remembered in the
dropped table, so it isn't fetched again) when it's older than the run's window,
undated on a first run, under the word floor (a source's min_words, else
steps.ingest.min_words), or the same text as a stored
story. The rest are stored with status "new". A source whose feed/listing fails
(or every article fetch of the run) counts a failure; steps.ingest.max_failures in a
row disable it. Cross-source "same story" de-duplication is pick.py's job, later.

Layout: base (records, errors), parse (feeds, listings, JSON, dates), article (extraction), run (one
source), commands, cli. Commands build their Fetcher through this package's namespace
(`Fetcher`), so tests can patch it here.
"""

from __future__ import annotations

from reelline.pipeline.fetch import Fetcher, FetchError
from reelline.pipeline.ingest.article import (
    detect_lang,
    extract_article,
    html_to_text,
    text_hash,
    word_count,
)
from reelline.pipeline.ingest.base import DATE_PARAMS, HTML_TYPES, Article, IngestError, Item, SourceError
from reelline.pipeline.ingest.cli import build_parser, main
from reelline.pipeline.ingest.commands import (
    CheckResult,
    check_source,
    cmd_report,
    cmd_reset,
    cmd_run,
    cmd_sources,
    open_db,
    print_summary,
)
from reelline.pipeline.ingest.parse import (
    item_date,
    min_words,
    parse_date,
    parse_feed,
    parse_items,
    parse_json,
    parse_json_date,
    parse_listing,
)
from reelline.pipeline.ingest.run import Ingest, SourceResult

__all__ = [
    "DATE_PARAMS",
    "HTML_TYPES",
    "Article",
    "CheckResult",
    "FetchError",
    "Fetcher",
    "Ingest",
    "IngestError",
    "Item",
    "SourceError",
    "SourceResult",
    "build_parser",
    "check_source",
    "cmd_report",
    "cmd_reset",
    "cmd_run",
    "cmd_sources",
    "detect_lang",
    "extract_article",
    "html_to_text",
    "item_date",
    "main",
    "min_words",
    "open_db",
    "parse_date",
    "parse_feed",
    "parse_items",
    "parse_json",
    "parse_json_date",
    "parse_listing",
    "print_summary",
    "text_hash",
    "word_count",
]
