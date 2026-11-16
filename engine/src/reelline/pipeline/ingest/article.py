"""Article extraction: the page's text, title, canonical link and date; word count, hash, language."""

from __future__ import annotations

import hashlib
import html
from datetime import datetime

import lxml.etree
import lxml.html
import trafilatura
from lxml.cssselect import CSSSelector

from reelline import language
from reelline.config import conf
from reelline.pipeline.dates import parse_date_text
from reelline.pipeline.ingest.base import BLOCK_TAGS, DATE_PARAMS, Article
from reelline.pipeline.ingest.parse import first_text, parse_date


def _canonical_href(doc) -> str | None:
    if doc is None:
        return None
    for link in doc.iter("link"):
        rel = (link.get("rel") or "").lower().split()
        if "canonical" in rel and link.get("href"):
            return link.get("href")
    return None


def extract_article(
    content: bytes, url: str, page_date_selector: str | None = None, now: datetime | None = None
) -> Article:
    try:
        page = lxml.html.document_fromstring(content)
    except (lxml.etree.ParserError, ValueError):
        page = None
    page_date_text = None
    if page_date_selector:
        page_date_text = first_text(page, CSSSelector(page_date_selector)) if page is not None else ""
    doc = trafilatura.bare_extraction(
        content,
        url=url,
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        date_extraction_params=DATE_PARAMS,
    )
    return Article(
        title=(" ".join(html.unescape(doc.title or "").split()) or None) if doc else None,
        text=(doc.text or "").strip() if doc else "",
        published=parse_date(doc.date) if doc else None,
        canonical_href=_canonical_href(page),
        page_date=parse_date_text(page_date_text, now) if page_date_text else None,
        page_date_text=page_date_text,
    )


def html_to_text(html: str) -> str:
    """Plain text from a feed entry's HTML, one line per block element."""
    if not html or not html.strip():
        return ""
    try:
        root = lxml.html.fragment_fromstring(html, create_parent="div")
    except (lxml.etree.ParserError, ValueError):
        return " ".join(html.split())
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.lower() in BLOCK_TAGS:
            el.tail = "\n" + (el.tail or "")
    lines = (" ".join(line.split()) for line in root.text_content().splitlines())
    return "\n".join(line for line in lines if line)


def word_count(text: str) -> int:
    return len(text.split())


def text_hash(text: str) -> str:
    """sha256 of the text with case and whitespace normalized."""
    return hashlib.sha256(" ".join(text.casefold().split()).encode("utf-8")).hexdigest()


def detect_lang(text: str, fallback: str) -> str:
    georgian = latin = 0
    for ch in text:
        if language.current().is_script_char(ch):
            georgian += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    letters = sum(ch.isalpha() for ch in text)
    if letters:
        if georgian / letters >= conf().steps.ingest.lang_min_share:
            return "ka"
        if latin / letters >= conf().steps.ingest.lang_min_share:
            return "en"
    return fallback
