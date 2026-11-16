"""Months: Asia/Tbilisi calendar months."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from reelline.core.errors import ReellineError
from reelline.pipeline import db
from reelline.pipeline.scoring import tbilisi


class CostsError(ReellineError, RuntimeError):
    """A bad month, or nothing to report on."""


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%B %Y")

    @property
    def short(self) -> str:
        return date(self.year, self.month, 1).strftime("%b %Y")

    def start(self) -> datetime:
        """Midnight on the 1st, Tbilisi time (aware)."""
        return datetime(self.year, self.month, 1, tzinfo=tbilisi())

    def next(self) -> Month:
        return Month(self.year + self.month // 12, self.month % 12 + 1)

    def prev(self) -> Month:
        return Month(self.year - (self.month == 1), (self.month - 2) % 12 + 1)

    def end(self) -> datetime:
        return self.next().start()

    def days(self) -> int:
        return (self.end() - self.start()).days

    def bounds_utc(self) -> tuple[str, str]:
        """[start, end) as llm_calls.called_at strings (UTC ISO, compared as text)."""
        return db.now_iso(self.start().astimezone(UTC)), db.now_iso(self.end().astimezone(UTC))


def parse_month(text: str) -> Month:
    try:
        year, month = (int(x) for x in text.strip().split("-"))
        if not (2000 <= year <= 9999 and 1 <= month <= 12) or len(text.strip()) != 7:
            raise ValueError
    except ValueError:
        raise CostsError(f"Not a month: {text!r} (YYYY-MM, e.g. 2026-09)") from None
    return Month(year, month)


def month_of(at: datetime) -> Month:
    local = at.astimezone(tbilisi())
    return Month(local.year, local.month)


def recent_months(at: datetime, n: int) -> list[Month]:
    """The month of `at` and the n - 1 before it, newest first."""
    out = [month_of(at)]
    while len(out) < n:
        out.append(out[-1].prev())
    return out


def local(at: str) -> datetime:
    """A stored UTC timestamp in Tbilisi time."""
    return datetime.fromisoformat(at).astimezone(tbilisi())
