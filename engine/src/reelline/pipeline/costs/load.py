"""Reading the cost ledger from the database."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.costs.model import Cost, Row, World, attribute, counts_for_reel, matches
from reelline.pipeline.costs.months import CostsError, Month
from reelline.pipeline.costs.report import Report, build_report
from reelline.pipeline.scoring import tbilisi


def load_rows(conn: sqlite3.Connection, month: Month) -> list[Row]:
    return _rows(conn, "called_at >= ? AND called_at < ?", month.bounds_utc())


def _rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[Row]:
    rows = conn.execute(
        f"""SELECT id, called_at, purpose, COALESCE(served_model, model) AS model, cost_usd, script_id, reel_id,
                   pick_id, job_id, manual, estimated, input_tokens, output_tokens, thinking_tokens, error
            FROM llm_calls WHERE {where} ORDER BY called_at, id""",
        params,
    ).fetchall()
    return [
        Row(
            r["id"],
            r["called_at"],
            r["purpose"],
            r["model"],
            r["cost_usd"],
            r["script_id"],
            r["reel_id"],
            r["pick_id"],
            r["job_id"],
            bool(r["manual"]),
            r["estimated"],
            r["input_tokens"],
            r["output_tokens"],
            r["thinking_tokens"],
            r["error"],
        )
        for r in rows
    ]


def load_world(conn: sqlite3.Connection) -> World:
    def table(sql: str) -> dict[int, dict]:
        return {r["id"]: dict(r) for r in conn.execute(sql).fetchall()}

    picks = table("SELECT id, story_id, slot_date, slot_time, status FROM picks")
    return World(
        reels=table("SELECT id, pick_id, script_id, name, status, created_at FROM reels"),
        scripts=table("SELECT id, pick_id, status FROM scripts"),
        picks=picks,
        jobs=table("SELECT id, kind, status FROM jobs"),
        titles={
            r["id"]: r["title"]
            for r in conn.execute("SELECT id, title FROM stories WHERE id IN (SELECT story_id FROM picks)").fetchall()
        },
    )


def sql_total(conn: sqlite3.Connection, month: Month) -> float:
    """The month's total straight from llm_calls (the report's own total must match)."""
    lo, hi = month.bounds_utc()
    return conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE called_at >= ? AND called_at < ?", (lo, hi)
    ).fetchone()[0]


def day_total(conn: sqlite3.Connection, day: date) -> float:
    """One Tbilisi day's spend (the evening summary), straight from llm_calls."""
    start = datetime(day.year, day.month, day.day, tzinfo=tbilisi())
    lo, hi = (db.now_iso(t.astimezone(UTC)) for t in (start, start + timedelta(days=1)))
    return conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE called_at >= ? AND called_at < ?", (lo, hi)
    ).fetchone()[0]


def month_costs(conn: sqlite3.Connection, month: Month) -> tuple[list[Cost], World]:
    world = load_world(conn)
    return attribute(load_rows(conn, month), world), world


def reel_costs(conn: sqlite3.Connection, reel_id: int) -> list[Cost]:
    """Every cost that counts under the reel, any month: its own render spend, its
    script's calls while it's the script's last reel, the brief while it's the pick's
    last reel (the report's attribution; manual dev spend left out)."""
    reel = conn.execute("SELECT script_id, pick_id FROM reels WHERE id = ?", (reel_id,)).fetchone()
    if reel is None:
        raise CostsError(f"No reel #{reel_id}")
    rows = _rows(
        conn,
        "reel_id = ? OR script_id = ? OR (script_id IS NULL AND pick_id = ?)",
        (reel_id, reel["script_id"], reel["pick_id"]),
    )
    return [c for c in attribute(rows, load_world(conn)) if counts_for_reel(c) == reel_id]


def reel_parts(costs: list[Cost]) -> dict:
    """{"total", "parts": [(label, usd)] in costs.reel_parts order after the rest,
    "upper_bound", "unpriced": models}: the review's cost line."""
    sums = defaultdict(float)
    for c in costs:
        label = next(
            (lab for lab, pats in conf().costs.reel_parts if matches(c.row.purpose, pats)), conf().costs.reel_parts_rest
        )
        sums[label] += c.usd
    labels = [conf().costs.reel_parts_rest] + [lab for lab, _ in conf().costs.reel_parts]
    return {
        "total": sum(c.usd for c in costs),
        "parts": [(lab, sums[lab]) for lab in labels],
        "upper_bound": any(c.row.purpose == "image_generate_upper_bound" for c in costs),
        "unpriced": sorted({c.row.model for c in costs if c.row.usd is None}),
    }


def report(conn: sqlite3.Connection, month: Month, now: datetime | None = None) -> Report:
    costs, world = month_costs(conn, month)
    return build_report(month, costs, world, sql_total(conn, month.prev()), now or datetime.now(UTC))
