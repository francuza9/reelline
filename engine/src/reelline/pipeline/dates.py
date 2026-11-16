"""Dates as Georgian sites write them, for listing cards and article pages.

parse_date_text() reads the first date in a short text (a date element's text, not
a whole article):
  - the language pack's month names in any case form, full or short: "25 <month> 2026",
    "29 <month>, 2026", "2026 <year word> 24 <month>", "19 <month>", "5 <short month>. 2026", and month
    first: "<month> 28, 2026"
  - numbers, day first (the Georgian order): "01.10.2026", "01/10/2026",
    "1-10-2026"; and ISO "2026-10-01"
A date without a year ("19 <month>") takes the latest year that doesn't put it
in the future (relative to `now`). Dates are days, not moments: a day becomes
steps.ingest.date_only_time Tbilisi time (UTC+channel.utc_offset_h, no DST),
which is the same calendar day in Tbilisi and in UTC.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone

from reelline import language
from reelline.config import conf

# Every written form of each month, from the channel's language pack.
MONTHS: dict[int, tuple[str, ...]] = language.current().month_forms
_LETTERS = language.current().letter_class
_MONTH_OF = {form: month for month, forms in MONTHS.items() for form in forms}
# Longest first, so a full month name wins over its short form; a whole word (a short form may end in ".").
_MONTH = (
    "(?P<month>" + "|".join(sorted(map(re.escape, _MONTH_OF), key=len, reverse=True)) + r")(?![" + _LETTERS + r"])\.?"
)

_YEAR_FIRST = (  # 2026 <year word> 24 <month>: only in a language with a word between the year and the day
    [
        re.compile(
            r"(?<!\d)(?P<year>\d{4})\s*" + re.escape(language.current().year_word) + r"\s+(?P<day>\d{1,2})\s*" + _MONTH
        )
    ]
    if language.current().year_word
    else []
)
_PATTERNS = (
    *_YEAR_FIRST,
    # 25 <month> 2026 / 29 <month>, 2026 / 19 <month>
    re.compile(r"(?<!\d)(?P<day>\d{1,2})\s*" + _MONTH + r"(?:\s*,?\s*(?P<year>\d{4})(?!\d))?"),
    # <month> 28, 2026 (month first)
    re.compile(r"(?<![" + _LETTERS + r"])" + _MONTH + r"\s+(?P<day>\d{1,2})(?!\d)\s*,?\s*(?P<year>\d{4})(?!\d)"),
    # 2026-10-01
    re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<nmonth>\d{1,2})-(?P<day>\d{1,2})(?!\d)"),
    # 01.10.2026 / 01/10/2026 / 1-10-2026
    re.compile(r"(?<!\d)(?P<day>\d{1,2})[./-](?P<nmonth>\d{1,2})[./-](?P<year>\d{4})(?!\d)"),
)


def tbilisi() -> timezone:
    return timezone(timedelta(hours=conf().channel.utc_offset_h))


def day_to_datetime(day: date) -> datetime:
    """A calendar day as an aware UTC datetime (steps.ingest.date_only_time Tbilisi time)."""
    hour, minute = (int(x) for x in conf().steps.ingest.date_only_time.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tbilisi()).astimezone(UTC)


def parse_day(text: str, now: datetime | None = None) -> date | None:
    """The first date in the text, as a calendar day; None if there's none (or it's invalid)."""
    if not text:
        return None
    text = " ".join(text.split())
    found = []
    for pattern in _PATTERNS:
        m = pattern.search(text)
        if m:
            found.append(m)
    for m in sorted(found, key=lambda m: m.start()):
        groups = m.groupdict()
        month = _MONTH_OF[groups["month"]] if groups.get("month") else int(groups["nmonth"])
        day = int(groups["day"])
        year = groups.get("year")
        try:
            if year:
                return date(int(year), month, day)
            if now is None:
                continue
            today = now.astimezone(tbilisi()).date()
            candidate = date(today.year, month, day)
            return candidate if candidate <= today + timedelta(days=1) else date(today.year - 1, month, day)
        except ValueError:
            continue  # 31 June, 13.13.2026
    return None


WEEKDAYS = language.current().weekdays


def georgian_day(day: date) -> str:
    """A day for the prompts: "2026-10-05 (5 <month>, <weekday>)"."""
    return f"{day.isoformat()} ({day.day} {MONTHS[day.month][0]}, {WEEKDAYS[day.weekday()]})"


def parse_date_text(text: str, now: datetime | None = None) -> datetime | None:
    """The first date in the text as an aware UTC datetime (see day_to_datetime)."""
    day = parse_day(text, now)
    return day_to_datetime(day) if day else None
