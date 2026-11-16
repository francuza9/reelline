"""The ingest commands: run, reset, sources, report."""

from __future__ import annotations

import contextlib
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline import ingest as _i
from reelline.pipeline.fetch import Fetcher, FetchError
from reelline.pipeline.ingest.article import extract_article, html_to_text, word_count
from reelline.pipeline.ingest.base import IngestError, SourceError
from reelline.pipeline.ingest.parse import item_date, min_words, parse_items
from reelline.pipeline.ingest.run import Ingest, SourceResult
from reelline.pipeline.sources import Source, by_id


def open_db(path: Path, readonly: bool) -> sqlite3.Connection:
    """The state database. Read-only (dry run, checks): an existing file opened with
    mode=ro, which must already be at the latest schema; no file gives an empty
    in-memory database."""
    if not readonly:
        return db.connect(path)
    if not Path(path).exists():
        return db.connect(Path(":memory:"))
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    version, latest = db.schema_version(conn), db.MIGRATIONS[-1].version
    if version != latest:
        conn.close()
        raise IngestError(f"{path} is at schema {version}, not {latest}: run a normal ingest to migrate it first")
    return conn


def _select(sources: list[Source], ids: list[str] | None) -> list[Source]:
    if not ids:
        chosen = [s for s in sources if s.enabled]
    else:
        registry = by_id(sources)
        unknown = [i for i in ids if i not in registry]
        if unknown:
            raise IngestError(f"Unknown source id(s): {', '.join(unknown)}")
        off = [i for i in ids if not registry[i].enabled]
        if off:
            raise IngestError(f"Disabled in sources.yaml: {', '.join(off)} (enable it there first)")
        chosen = [registry[i] for i in dict.fromkeys(ids)]
    return sorted(chosen, key=lambda s: s.priority)  # stable: file order within a priority


def _dropped_text(dropped: Counter) -> str:
    return ", ".join(f"{reason} {n}" for reason, n in sorted(dropped.items())) or "-"


def print_summary(results: list[SourceResult], out=print) -> None:
    out("")
    out(f"{'source':<24} {'status':<13} {'seen':>4} {'new':>4} {'known':>5}  dropped / errors")
    for r in results:
        extra = _dropped_text(r.dropped)
        if r.article_errors:
            extra += f"; {len(r.article_errors)} article errors"
        if r.error:
            extra += f"; {r.error}"
        if r.note and r.note != r.error:
            extra += f"; {r.note}"
        status = r.status + ("*" if r.capped else "")
        out(f"{r.source_id:<24} {status:<13} {r.items_seen:>4} {r.new:>4} {r.known:>5}  {extra}")
    total: Counter[str] = Counter()
    for r in results:
        total.update(r.dropped)
    out(
        f"{'total':<24} {'':<13} {sum(r.items_seen for r in results):>4} {sum(r.new for r in results):>4} "
        f"{sum(r.known for r in results):>5}  {_dropped_text(total)}"
    )
    if any(r.capped for r in results):
        out(f"* capped at {conf().steps.ingest.max_items_per_source} items; the rest are seen next run")


def cmd_run(args, sources: list[Source]) -> int:
    chosen = _select(sources, args.source)
    if args.since_days is not None and args.since_days < 1:
        raise IngestError("--since-days must be at least 1")
    conn = open_db(args.db, readonly=args.dry_run)
    ingest = Ingest(conn, _i.Fetcher(), dry_run=args.dry_run, since_days=args.since_days)
    if not args.dry_run:
        ingest.run_id = db.start_run(conn, {"sources": [s.id for s in chosen], "since_days": args.since_days})
    results: list[SourceResult] = []
    try:
        for source in chosen:
            results.append(ingest.run_source(source))
    except BaseException as exc:
        if ingest.run_id is not None:
            # the database is what failed; the original error says why
            with contextlib.suppress(sqlite3.Error, db.DbError):
                db.finish_run(
                    conn,
                    ingest.run_id,
                    "crashed",
                    {r.source_id: r.to_json() for r in results},
                    {"_crash": f"{type(exc).__name__}: {exc}"},
                )
        raise
    failed = [r for r in results if r.status == "failed" or (r.status == "disabled" and r.error)]
    # Sources skipped as disabled in the database weren't tried.
    tried = [r for r in results if not (r.status == "disabled" and not r.error)]
    if ingest.run_id is not None:
        db.finish_run(
            conn,
            ingest.run_id,
            "errors" if failed else "ok",
            {r.source_id: r.to_json() for r in results},
            {r.source_id: r.error for r in failed},
        )
    print_summary(results)
    if args.dry_run:
        print("dry run: nothing written")
    if failed:
        print(f"{len(failed)} of {len(tried)} sources failed: {', '.join(r.source_id for r in failed)}")
    if args.strict:
        return 1 if failed else 0
    return 1 if tried and len(failed) == len(tried) else 0


def cmd_reset(args, sources: list[Source]) -> int:
    conn = open_db(args.db, readonly=False)
    registry = by_id(sources)
    for source_id in args.reset:
        if source_id not in registry and db.get_source(conn, source_id) is None:
            raise IngestError(f"Unknown source id: {source_id}")
        row = db.get_source(conn, source_id)
        was = row["disabled_reason"] if row is not None else None
        db.reset_source(conn, source_id)
        print(f"{source_id}: reset{f' (was disabled: {was})' if was else ' (was not disabled)'}")
    return 0


@dataclass
class CheckResult:
    status: str  # ok | warn | FAIL
    found: bool = False  # a feed with entries / a listing whose selector found links
    items: int = 0
    sample: str = "-"
    error: str | None = None


def check_source(fetcher: Fetcher, source: Source) -> CheckResult:
    try:
        if not fetcher.allowed(source.url, source.headers):
            return CheckResult("FAIL", error=f"robots.txt disallows {source.url}")
        page = fetcher.get(source.url, extra_headers=source.headers)
        items = parse_items(source, page, datetime.now(UTC))
    except (FetchError, SourceError) as exc:
        return CheckResult("FAIL", error=str(exc))
    if not items:
        return CheckResult("FAIL", found=False, error="no items")
    result = CheckResult("ok", found=True, items=len(items))
    first = items[0]
    if source.item_text:
        words = word_count(html_to_text(first.content or ""))
        published, date_source = item_date(source, first)
    else:
        try:
            if not fetcher.allowed(first.url):
                result.status, result.sample = "warn", "article disallowed by robots.txt"
                return result
            article_page = fetcher.get(first.url)
        except FetchError as exc:
            result.status, result.sample = "warn", f"article: {exc}"
            return result
        article = extract_article(article_page.content, article_page.url, source.page_date_selector, datetime.now(UTC))
        words = word_count(article.text)
        published, date_source = item_date(source, first, article)
    dated = f"dated {published.date().isoformat()} ({date_source})" if published else "undated"
    unread = first.date_text if first.date_text is not None and first.published is None else None
    if unread is not None:
        dated += f", listing date not read: {unread!r}"
    result.sample = f"{words} words, {dated}"
    if words < min_words(source):
        result.status = "warn"
        result.sample += f" (under {min_words(source)})"
    return result


def cmd_sources(args, sources: list[Source]) -> int:
    conn = open_db(args.db, readonly=True)
    if not args.check:
        for s in sources:
            row = db.get_source(conn, s.id)
            state = f"db-disabled: {row['disabled_reason']}" if row is not None and row["disabled_reason"] else ""
            print(
                f"{s.id:<24} {s.type:<8} {'on ' if s.enabled else 'off'} p{s.priority} {s.topic:<10} "
                f"{s.lang} {s.url} {state}"
            )
        return 0
    fetcher = _i.Fetcher(log=lambda message: None)
    failing = []
    print(f"{'source':<24} {'yaml':<4} {'type':<8} {'status':<6} {'found':<5} {'items':>5}  sample / error")
    for s in sources:
        r = check_source(fetcher, s)
        row = db.get_source(conn, s.id)
        db_note = f" [db-disabled: {row['disabled_reason']}]" if row is not None and row["disabled_reason"] else ""
        print(
            f"{s.id:<24} {'on' if s.enabled else 'off':<4} {s.type:<8} {r.status:<6} "
            f"{'yes' if r.found else 'no':<5} {r.items:>5}  {(r.error or r.sample)[:200]}{db_note}",
            flush=True,
        )
        if s.enabled and r.status == "FAIL":
            failing.append(s.id)
    if failing:
        print(f"\nFailing enabled sources: {', '.join(failing)}")
        return 1
    return 0


def cmd_report(args, sources: list[Source]) -> int:
    conn = open_db(args.db, readonly=True)
    days = conf().steps.ingest.report_days
    since = db.now_iso(datetime.now(UTC) - timedelta(days=days))
    recent = db.story_counts_by_source(conn, since)
    total = db.story_counts_by_source(conn, "")
    rows = {r["id"]: r for r in db.all_sources(conn)}
    ids = [s.id for s in sources] + sorted(set(rows) - {s.id for s in sources})
    registry = by_id(sources)
    print(f"{'source':<24} {'yaml':<4} {f'{days}d':>4} {'all':>5} {'fails':>5}  {'last ok':<25} state")
    for sid in ids:
        row = rows.get(sid)
        yaml_state = ("on" if registry[sid].enabled else "off") if sid in registry else "gone"
        last_ok = (row["last_ok_at"] if row is not None else None) or "never"
        fails = row["consecutive_failures"] if row is not None else 0
        state = ""
        if row is not None and row["disabled_reason"]:
            state = f"DISABLED: {row['disabled_reason']}"
        elif row is not None and row["last_error"] and fails:
            state = f"last error: {row['last_error']}"
        print(
            f"{sid:<24} {yaml_state:<4} {recent.get(sid, 0):>4} {total.get(sid, 0):>5} {fails:>5}  "
            f"{last_ok:<25} {state}"
        )
    print(f"\nStories fetched in the last {days} days, by status and topic:")
    counts = db.story_counts_by_status_topic(conn, since)
    if not counts:
        print("  (none)")
    for r in counts:
        print(f"  {r['status']:<9} {r['topic_hint']:<10} {r['n']:>5}")
    runs = db.last_runs(conn, 1)
    if runs:
        r = runs[0]
        print(f"\nLast run: #{r['id']} {r['started_at']} -> {r['finished_at'] or '-'}  {r['status']}")
    return 0
