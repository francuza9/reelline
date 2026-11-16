"""One source's run: fetch, filter, drop or store each item."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.fetch import Fetcher, FetchError
from reelline.pipeline.ingest.article import detect_lang, extract_article, html_to_text, text_hash, word_count
from reelline.pipeline.ingest.base import HTML_TYPES, Item, SourceError
from reelline.pipeline.ingest.parse import item_date, min_words, parse_items
from reelline.pipeline.sources import Source
from reelline.pipeline.urls import UrlError, normalize_url, prefer_canonical


@dataclass
class SourceResult:
    source_id: str
    status: str = "ok"  # ok | not_modified | failed | disabled
    items_seen: int = 0
    new: int = 0
    known: int = 0
    dropped: Counter = field(default_factory=Counter)
    error: str | None = None  # the failure that failed the source's run
    article_errors: list[str] = field(default_factory=list)  # retried next run
    articles_fetched: int = 0
    undated: int = 0
    date_unparsed: int = 0  # a date element whose text isn't a date we can read
    capped: bool = False
    note: str | None = None

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "items_seen": self.items_seen,
            "new": self.new,
            "known": self.known,
            "dropped": dict(self.dropped),
            "article_errors": len(self.article_errors),
            "undated": self.undated,
            "date_unparsed": self.date_unparsed,
            "capped": self.capped,
            "error": self.error,
            "note": self.note,
        }


class Ingest:
    def __init__(
        self,
        conn: sqlite3.Connection,
        fetcher: Fetcher,
        *,
        dry_run: bool = False,
        since_days: int | None = None,
        run_id: int | None = None,
        now: datetime | None = None,
        log: Callable[[str], None] = lambda message: print(message, flush=True),
    ):
        self.conn = conn
        self.fetcher = fetcher
        self.dry_run = dry_run
        self.since_days = since_days
        self.run_id = run_id
        self.now = now or datetime.now(UTC)
        self.log = log
        # What this run has stored or dropped, for de-dup when nothing is written (dry run).
        self._seen_urls: set[str] = set()
        self._seen_hashes: set[str] = set()

    # -- state helpers --

    def _known(self, *urls: str) -> bool:
        return any(u in self._seen_urls for u in urls) or db.known_url(self.conn, *urls) is not None

    def _drop(
        self, source: Source, res: SourceResult, url: str, canonical: str | None, reason: str, detail: str | None = None
    ) -> None:
        res.dropped[reason] += 1
        self.log(f"    drop {reason}{f' ({detail})' if detail else ''}: {url}")
        self._seen_urls.update(u for u in (url, canonical) if u)
        if not self.dry_run:
            db.record_dropped(self.conn, source.id, url, canonical, reason, detail)

    def _fail(self, source: Source, res: SourceResult, error: str) -> None:
        res.status, res.error = "failed", error
        self.log(f"  FAILED: {error}")
        if self.dry_run:
            return
        if db.record_source_failure(self.conn, source.id, error, conf().steps.ingest.max_failures):
            res.note = f"disabled after {conf().steps.ingest.max_failures} failures in a row"
            self.log(f"  {source.id} disabled: {res.note}")

    # -- the run --

    def run_source(self, source: Source) -> SourceResult:
        res = SourceResult(source.id)
        if not source.assess and not self.dry_run:
            # The tech news archive: stories from before it, still new, are archived too.
            archived = db.archive_source_stories(self.conn, source.id)
            if archived:
                self.log(f"{source.id}: {archived} earlier stories archived (assess: false)")
        state = db.get_source(self.conn, source.id)
        if state is not None and state["disabled_reason"]:
            res.status, res.note = "disabled", state["disabled_reason"]
            self.log(f"{source.id}: disabled in the database ({state['disabled_reason']}); --reset {source.id}")
            return res
        first_run = state is None or state["last_ok_at"] is None
        window = (
            self.since_days
            if self.since_days is not None
            else (conf().steps.ingest.first_run_days if first_run else None)
        )
        cutoff = self.now - timedelta(days=window) if window is not None else None
        drop_undated = first_run and cutoff is not None and not conf().steps.ingest.first_run_keep_undated
        self.log(
            f"{source.id}: {source.type} {source.url}"
            f"{' (first run)' if first_run else ''}{f', last {window} days' if window else ''}"
        )

        try:
            if not self.fetcher.allowed(source.url, source.headers):
                reason = f"robots.txt disallows {source.url}"
                res.status, res.error, res.note = "disabled", reason, reason
                self.log(f"  disabled: {reason}")
                if not self.dry_run:
                    db.disable_source(self.conn, source.id, reason)
                return res
            use_validators = state is not None and not self.dry_run
            page = self.fetcher.get(
                source.url,
                etag=state["etag"] if use_validators else None,
                last_modified=state["last_modified"] if use_validators else None,
                extra_headers=source.headers,
            )
            if page.not_modified:
                res.status = "not_modified"
                self.log("  not modified (304)")
                if not self.dry_run:
                    db.record_source_ok(self.conn, source.id, page.etag, page.last_modified)
                return res
            items = parse_items(source, page, self.now)
        except (FetchError, SourceError) as exc:
            self._fail(source, res, str(exc))
            return res

        res.items_seen = len(items)
        todo = 0
        for item in items:
            try:
                url = normalize_url(item.url)
            except UrlError:
                res.dropped["bad_url"] += 1
                self.log(f"    drop bad_url: {item.url!r}")
                continue
            if self._known(url):
                res.known += 1
                continue
            if todo == conf().steps.ingest.max_items_per_source:
                res.capped = True
                continue
            todo += 1
            self._item(source, item, url, res, cutoff, drop_undated)

        if res.article_errors and res.articles_fetched == 0:
            self._fail(
                source, res, f"every article fetch failed ({len(res.article_errors)}), last: {res.article_errors[-1]}"
            )
            return res
        if not self.dry_run:
            # Validators only when every item was seen and settled; otherwise the next run
            # must get the full feed again to retry the rest.
            complete = not res.capped and not res.article_errors
            db.record_source_ok(
                self.conn, source.id, page.etag if complete else None, page.last_modified if complete else None
            )
        return res

    def _item(
        self, source: Source, item: Item, url: str, res: SourceResult, cutoff: datetime | None, drop_undated: bool
    ) -> None:
        """One item not seen before (url: its normalized URL)."""
        if item.date_text is not None and item.published is None:
            res.date_unparsed += 1
            self.log(f"    listing date not read: {item.date_text!r}: {url}")
        if cutoff is not None and item.published is not None and item.published < cutoff:
            self._drop(source, res, url, None, "before_window", item.published.date().isoformat())
            return

        if source.item_text:
            title, text, canonical = item.title, html_to_text(item.content or ""), url
            published, date_source = item_date(source, item)
        else:
            try:
                if not self.fetcher.allowed(item.url):
                    self._drop(source, res, url, None, "robots")
                    return
                page = self.fetcher.get(item.url)
            except FetchError as exc:
                if exc.status in conf().steps.ingest.permanent_statuses:
                    self._drop(source, res, url, None, f"http_{exc.status}")
                else:
                    res.article_errors.append(str(exc))
                    self.log(f"    error (retried next run): {exc}")
                return
            res.articles_fetched += 1
            content_type = page.content_type.split(";")[0].strip().lower()
            if content_type and content_type not in HTML_TYPES:
                self._drop(source, res, url, None, "not_html", content_type)
                return
            article = extract_article(page.content, page.url, source.page_date_selector, self.now)
            if article.page_date_text is not None and article.page_date is None:
                res.date_unparsed += 1
                self.log(f"    page date not read: {article.page_date_text!r}: {url}")
            canonical = prefer_canonical(page.url, article.canonical_href)
            if canonical != url and self._known(canonical):
                res.known += 1
                self._seen_urls.add(url)
                if not self.dry_run:
                    db.record_dropped(self.conn, source.id, url, canonical, "known_canonical")
                return
            # A feed's title is the clean headline; a page's <title> often carries the site
            # name ("... | TechCrunch"). A listing's link text can hold dates and teasers.
            title = (item.title or article.title) if source.type == "rss" else (article.title or item.title)
            text = article.text
            published, date_source = item_date(source, item, article)

        if published is not None and published > self.now + timedelta(hours=conf().steps.ingest.max_future_skew_h):
            self.log(f"    ignoring future date {published.isoformat()}: {url}")
            published, date_source = None, None
        if published is None:
            res.undated += 1
            if drop_undated:
                self._drop(source, res, url, canonical, "undated_first_run")
                return
        if cutoff is not None and published is not None and published < cutoff:
            self._drop(source, res, url, canonical, "before_window", published.date().isoformat())
            return
        words = word_count(text)
        if words < min_words(source):
            self._drop(source, res, url, canonical, "too_short", f"{words} words")
            return
        digest = text_hash(text)
        if digest in self._seen_hashes or db.known_hash(self.conn, digest):
            self._drop(source, res, url, canonical, "duplicate_text")
            return
        if not title:
            self._drop(source, res, url, canonical, "no_title")
            return

        # No date anywhere: the first-seen time stands in (a later run's new URL is new),
        # marked as such so pick can tell.
        if published is None:
            published, date_source = self.now, "first_seen"
        story = db.NewStory(
            source_id=source.id,
            url=url,
            canonical_url=canonical,
            title=title,
            published_at=db.now_iso(published),
            date_source=date_source,
            fetched_at=db.now_iso(self.now),
            lang=detect_lang(text, source.lang),
            text=text,
            text_hash=digest,
            word_count=words,
            topic_hint=source.topic,
            run_id=self.run_id,
            status="new" if source.assess else "archived",
        )
        self._seen_urls.update((url, canonical))
        self._seen_hashes.add(digest)
        res.new += 1
        self.log(f"    new ({words} words, {story.published_at} {date_source}): {title[:80]}")
        if not self.dry_run:
            db.insert_story(self.conn, story)
