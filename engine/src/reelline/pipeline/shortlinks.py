"""Shortlinks that never expire: <SHORTLINK_BASE>/<code> (<site>/s/<code>), one
static page per code on our own Cloudflare Pages site (the credits site,
reelline/pipeline/credits.py build_site()), redirecting to its target with a meta refresh, a
rel=canonical and a plain link: no scripts, no tracking.

Why a page per code and not Pages' _redirects file: _redirects holds at most 2,000
static redirects (developers.cloudflare.com/pages/configuration/redirects, checked
2026-10-16), and at a few links a day that's under 3 years. A page per code counts
against the 20,000 files a free Pages site may hold (pages/platform/limits): with the
credits pages, about 3 files a day, 18 years.

Every link ever made is in the shortlinks table (migration 21: code, target, the reel,
its kind, created_at) and every deploy renders all of them, so a published link never
breaks; a code's target never changes. Codes: publish.shortlinks.code_len random
characters (secrets) of publish.shortlinks.alphabet (base62 without 0 O 1 l I), unique (a
collision draws again). Targets: tracking parameters stripped (reelline/pipeline/urls.py
strip_tracking()).

link_for() makes (once) a reel's link of a kind (source | credits) to a target;
caption_url() is what captions and comments show (no scheme), url() the live address.
reelline/pipeline/caption.py puts the source link in the caption; reelline/pipeline/credits.py
publish_site() deploys and checks every one of the reel's links live before it posts
(check_link(): HTTP 200, the code's marker, the refresh going exactly to the target).

Usage:
  python -m reelline.pipeline.shortlinks list [--reel N]
  python -m reelline.pipeline.shortlinks deploy <reel_id>
      The reel's source link (made if missing), the whole site built and deployed, each
      of the reel's links checked live (publish_site()). Posts nothing.
"""

from __future__ import annotations

import argparse
import html
import re
import secrets
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.pipeline import db
from reelline.pipeline.urls import strip_tracking

KINDS = ("source", "credits")
_REFRESH = re.compile(r'<meta http-equiv="refresh" content="0; url=([^"]*)">')


class ShortlinkError(ReellineError, RuntimeError):
    """A link that can't be made, or isn't live where it should be."""


def new_code() -> str:
    return "".join(
        secrets.choice(conf().publish.shortlinks.alphabet) for _ in range(conf().publish.shortlinks.code_len)
    )


def caption_url(code: str) -> str:
    """What a caption or a comment shows: <SHORTLINK_BASE>/<code>, no scheme."""
    return f"{conf().publish.shortlinks.base}/{code}"


def url(code: str) -> str:
    return f"https://{caption_url(code)}"


def link_for(
    conn: sqlite3.Connection, reel_id: int, kind: str, target: str, draw: Callable[[], str] = new_code
) -> sqlite3.Row:
    """The reel's link of this kind to `target` (tracking parameters stripped), made the
    first time it's asked for; the same row every time after."""
    if kind not in KINDS:
        raise ShortlinkError(f"Unknown link kind {kind!r}")
    target = strip_tracking(target)
    if urlsplit(target).scheme not in ("http", "https"):
        raise ShortlinkError(f"Not an http(s) target: {target!r}")
    row = conn.execute(
        "SELECT * FROM shortlinks WHERE reel_id = ? AND kind = ? AND target = ? ORDER BY created_at LIMIT 1",
        (reel_id, kind, target),
    ).fetchone()
    if row is not None:
        return row
    for _ in range(conf().publish.shortlinks.max_tries):
        code = draw()
        with db.transaction(conn):
            made = conn.execute(
                """INSERT OR IGNORE INTO shortlinks (code, target, reel_id, kind, created_at)
                                   VALUES (?, ?, ?, ?, ?)""",
                (code, target, reel_id, kind, db.now_iso()),
            ).rowcount
        if made:
            return conn.execute("SELECT * FROM shortlinks WHERE code = ?", (code,)).fetchone()
    raise ShortlinkError(f"No free code after {conf().publish.shortlinks.max_tries} draws")


def reel_links(conn: sqlite3.Connection, reel_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM shortlinks WHERE reel_id = ? ORDER BY created_at, code", (reel_id,)).fetchall()


def page_html(code: str, target: str) -> str:
    """The redirect page: a meta refresh, rel=canonical and a plain link to the target;
    no scripts, no tracking, not indexed."""
    t = html.escape(target, quote=True)
    host = html.escape(urlsplit(target).hostname or target, quote=True)
    return (
        f'<!doctype html><html lang="{conf().channel.language}"><head><meta charset="utf-8"><meta name="robots" content="noindex">'
        f'<meta name="{conf().channel.name}-link" content="{html.escape(code, quote=True)}"><link rel="canonical" href="{t}">'
        f'<meta http-equiv="refresh" content="0; url={t}"><title>{host}</title></head>'
        f'<body><p><a href="{t}">{t}</a></p></body></html>\n'
    )


def write_pages(conn: sqlite3.Connection, out_dir: Path) -> list[Path]:
    """Every link ever made, as s/<code>.html under out_dir (served at /s/<code>)."""
    (out_dir / "s").mkdir(parents=True, exist_ok=True)
    written = []
    for row in conn.execute("SELECT code, target FROM shortlinks ORDER BY created_at, code"):
        path = out_dir / "s" / f"{row['code']}.html"
        path.write_text(page_html(row["code"], row["target"]), encoding="utf-8")
        written.append(path)
    return written


def refresh_target(page: str) -> str | None:
    m = _REFRESH.search(page)
    return html.unescape(m.group(1)) if m else None


def check_link(
    conn: sqlite3.Connection | None,
    code: str,
    target: str,
    http,
    sleep: Callable[[float], None] = time.sleep,
    base: str | None = None,
    timeout_s: float | None = None,
) -> str:
    """The live link answers 200 with its own marker and redirects exactly to `target`,
    within `timeout_s` (publish.credits_page.verify_timeout_s; 0: one look); its URL
    (verified_at recorded with conn). With `base` (a deployment's own address) it's
    checked there, at /s/<code>, instead of its caption address."""
    address, marker = f"{base}/s/{code}" if base else url(code), f'name="{conf().channel.name}-link" content="{code}"'
    timeout_s = conf().publish.credits_page.verify_timeout_s if timeout_s is None else timeout_s
    waited, last = 0.0, None
    while True:
        try:
            r = http.get(address, follow_redirects=True, timeout=30)
            if r.status_code == 200 and marker in r.text:
                got = refresh_target(r.text)
                if got == target:
                    if conn is not None:
                        with db.transaction(conn):
                            conn.execute("UPDATE shortlinks SET verified_at = ? WHERE code = ?", (db.now_iso(), code))
                    return address
                raise ShortlinkError(f"{address} redirects to {got!r}, not {target!r}")
            last = f"HTTP {r.status_code}"
        except ShortlinkError:
            raise
        except Exception as exc:  # network
            last = f"{type(exc).__name__}: {exc}"
        if waited >= timeout_s:
            raise ShortlinkError(f"{address} not live after {waited:.0f}s ({last})")
        sleep(conf().publish.credits_page.verify_poll_s)
        waited += conf().publish.credits_page.verify_poll_s


def caption_codes(text: str) -> list[str]:
    if not conf().publish.shortlinks.enabled:
        return []
    alphabet = re.escape(conf().publish.shortlinks.alphabet)
    return re.findall(rf"{re.escape(conf().publish.shortlinks.base)}/([{alphabet}]+)", text or "")


def caption_problems(conn: sqlite3.Connection, caption: str | None, reel_id: int) -> list[str]:
    """Every shortlink in the caption must be one of this reel's links (empty: fine). A
    caption without any (assembled before shortlinks) is fine too."""
    mine = {r["code"] for r in reel_links(conn, reel_id)}
    return [
        f"{caption_url(code)} in the caption isn't one of reel #{reel_id}'s links"
        for code in caption_codes(caption or "")
        if code not in mine
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reelline.pipeline.shortlinks", description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list", help="every link (or one reel's)")
    p.add_argument("--reel", type=int)
    p = sub.add_parser("deploy", help="the reel's links made, the site deployed, each checked live (posts nothing)")
    p.add_argument("reel_id", type=int)
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    if args.command == "list":
        rows = (
            reel_links(conn, args.reel)
            if args.reel is not None
            else conn.execute("SELECT * FROM shortlinks ORDER BY created_at, code").fetchall()
        )
        for r in rows:
            print(
                f"{caption_url(r['code'])}  reel #{r['reel_id']} {r['kind']:<7} {r['target']}  "
                f"(made {r['created_at'][:16]}, checked {(r['verified_at'] or '-')[:16]})"
            )
        return 0
    from reelline.pipeline.credits import CreditsError, publish_site

    reel = conn.execute(
        "SELECT r.id, s.canonical_url FROM reels r JOIN scripts sc ON sc.id = r.script_id "
        "JOIN stories s ON s.id = sc.story_id WHERE r.id = ?",
        (args.reel_id,),
    ).fetchone()
    if reel is None:
        print(f"FAIL: no reel #{args.reel_id}", file=sys.stderr)
        return 1
    link_for(conn, reel["id"], "source", reel["canonical_url"])
    try:
        result = publish_site(conn, reel["id"])
    except (CreditsError, ShortlinkError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    for address in result.get("links", []):
        print(f"Live: {address}")
    if result.get("credits"):
        print(f"Live: {result['credits']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
