"""License allowlist for Wikimedia Commons files, and attribution text.

classify_commons() reads a file's imageinfo `extmetadata` and returns a
LicenseVerdict. Allowed: CC0, public domain (incl. PD-* variants), CC BY (any
version), CC BY-SA (any version). Rejected: anything NC or ND, fair use / non-free,
any other license (GFDL-only, Apache, ...), and a missing or unparseable license.
Logos (PD-textlogo, trademarked files) are allowed but flagged editorial_only:
the copyright may be free, the trademark isn't.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from reelline.media.images.config import ATTRIBUTION

_CC_BY = re.compile(r"^cc[ -]by(?P<sa>[ -]sa)?(?:[ -](?P<ver>\d(?:\.\d)?))?(?:[ -][a-z]{2,3}(?:[ -]\w+)?)?$")
_CC0 = re.compile(r"^cc0(?:[ -]1\.0)?$")
_PD = re.compile(r"^(?:pd|public domain|pd-[\w-]+|cc-pd-mark|cc pdm(?: 1\.0)?|pdm)$")
_NON_FREE = re.compile(r"\bnc\b|\bnd\b|noncommercial|non-commercial|noderiv|fair use|non-free")


@dataclass(frozen=True)
class LicenseVerdict:
    ok: bool
    family: str | None  # "cc0" | "pd" | "cc-by" | "cc-by-sa" when ok
    name: str  # short name as shown in attributions, e.g. "CC BY-SA 4.0"
    url: str | None
    attribution_required: bool
    editorial_only: bool
    reason: str  # why it was rejected, or "" when ok


def meta_value(extmetadata: dict, key: str) -> str:
    """A plain-text extmetadata value ("" when absent)."""
    entry = extmetadata.get(key) or {}
    value = entry.get("value") if isinstance(entry, dict) else entry
    return strip_html(str(value)) if value is not None else ""


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def classify_license_name(name: str) -> str | None:
    """ "cc0" | "pd" | "cc-by" | "cc-by-sa" for an allowed license name, else None."""
    n = re.sub(r"\s+", " ", name.strip().lower().replace("_", " "))
    if not n or _NON_FREE.search(n):
        return None
    if _CC0.match(n):
        return "cc0"
    if _PD.match(n):
        return "pd"
    m = _CC_BY.match(n)
    if m:
        return "cc-by-sa" if m.group("sa") else "cc-by"
    return None


def classify_commons(extmetadata: dict) -> LicenseVerdict:
    short = meta_value(extmetadata, "LicenseShortName")
    code = meta_value(extmetadata, "License")
    url = meta_value(extmetadata, "LicenseUrl") or None
    usage = meta_value(extmetadata, "UsageTerms")
    categories = meta_value(extmetadata, "Categories").lower()
    restrictions = meta_value(extmetadata, "Restrictions").lower()
    non_free = meta_value(extmetadata, "NonFree").lower() == "true"
    attribution = meta_value(extmetadata, "AttributionRequired").lower()

    def reject(reason: str) -> LicenseVerdict:
        return LicenseVerdict(False, None, short or code or "?", url, False, False, reason)

    if non_free:
        return reject("non-free file")
    if "fair use" in categories or "non-free" in categories:
        return reject("fair use / non-free category")
    if not short and not code:
        return reject("missing license")
    family = classify_license_name(short) if short else None
    if family is None and code and not short:
        family = classify_license_name(code)
    if family is None:
        return reject(f"license not in allowlist: {short or code!r}")
    # A code that contradicts the short name (e.g. short "CC BY-SA 4.0" but code
    # "cc-by-nc-4.0") is unparseable, not free. Unknown codes are fine.
    if code and (_NON_FREE.search(code.lower()) or classify_license_name(code) not in (None, family)):
        return reject(f"license fields disagree: {short!r} vs {code!r}")
    if attribution not in ("true", "false", ""):
        return reject(f"unparseable AttributionRequired {attribution!r}")
    editorial = (
        "trademark" in restrictions
        or "textlogo" in categories.replace(" ", "")
        or "with trademark" in categories
        or "textlogo" in usage.lower().replace(" ", "")
    )
    name = {"cc0": "CC0", "pd": "Public domain"}.get(family, short)
    required = attribution == "true" or family in ("cc-by", "cc-by-sa")
    return LicenseVerdict(True, family, name, url, required, editorial, "")


def short_author(author: str) -> str:
    author = strip_html(author)
    limit = ATTRIBUTION["author_max_chars"]
    return author if len(author) <= limit else author[: limit - 1].rstrip() + "…"


def attribution_line(source: str, author: str, license_name: str) -> str:
    """Caption-ready credit line; source is "commons", "pixabay", "pexels" or "manual"."""
    return ATTRIBUTION[source].format(author=short_author(author) or "unknown", license=license_name)
