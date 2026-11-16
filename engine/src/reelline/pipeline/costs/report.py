"""The report and the CSV (pure functions over attributed costs)."""

from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from reelline.config import conf
from reelline.pipeline.costs.model import Cost, World, counts_for_reel
from reelline.pipeline.costs.months import Month, local
from reelline.pipeline.scoring import tbilisi


@dataclass
class Report:
    month: Month
    total: float
    prev_total: float
    rows: int
    # The current month: so_far, day, days, estimate; with an approved reel also
    # reels_per_day and projection.
    pace: dict | None = None
    providers: list[tuple[str, float]] = field(default_factory=list)
    google: list[tuple[str, float]] = field(default_factory=list)
    steps: list[tuple[str, float]] = field(default_factory=list)
    reel_counts: list[tuple[str, int]] = field(default_factory=list)
    outcomes: list[tuple[str, float]] = field(default_factory=list)
    waste: list[tuple[str, float]] = field(default_factory=list)
    outcome_usd: dict[str, float] = field(default_factory=dict)
    approved: int = 0
    per_approved: float | None = None
    manual: float = 0.0  # manual dev spend, left out of per_approved
    median_run: float | None = None
    runs: int = 0
    top: list[dict] = field(default_factory=list)
    daily: list[tuple[date, float]] = field(default_factory=list)
    estimated: float = 0.0
    unpriced: list[str] = field(default_factory=list)  # models of rows with no cost
    upper_bound: bool = False
    failed_tries: int = 0  # tries whose connection dropped, and their cost (retries)
    failed_usd: float = 0.0


def _sum_by(costs: list[Cost], key) -> dict:
    out: dict = defaultdict(float)
    for c in costs:
        out[key(c)] += c.usd
    return out


def build_report(month: Month, costs: list[Cost], world: World, prev_total: float, now: datetime) -> Report:
    total = sum(c.usd for c in costs)
    r = Report(month, total, prev_total, len(costs))
    start, end = month.start(), month.end()
    if start <= now < end:
        day = now.astimezone(tbilisi()).day
        r.pace = {"so_far": total, "day": day, "days": month.days(), "estimate": total / day * month.days()}

    by_provider = _sum_by(costs, lambda c: c.provider)
    r.providers = [(label, by_provider.get(key, 0.0)) for key, label, _ in conf().costs.providers]
    r.providers.append((conf().costs.other_provider, by_provider.get("other", 0.0)))
    by_kind = _sum_by([c for c in costs if c.provider == "google"], lambda c: c.google_kind)
    kinds = [conf().costs.google_text] + [label for label, _ in conf().costs.google_kinds]
    r.google = [(k, by_kind[k]) for k in kinds if by_kind.get(k)]

    by_step = _sum_by(costs, lambda c: c.step)
    step_labels = {key: label for key, label, _ in conf().costs.steps}
    r.steps = [(label, by_step.get(key, 0.0)) for key, label, _ in conf().costs.steps]

    # Reels made this month, by final status.
    made = [rid for rid, reel in world.reels.items() if start <= local(reel["created_at"]) < end]
    statuses = defaultdict(int)
    for rid in made:
        statuses[world.reel_status(rid)] += 1
    r.reel_counts = [(label, statuses.get(key, 0)) for key, label in conf().costs.reel_statuses]
    r.approved = sum(n for key, n in statuses.items() if conf().costs.reel_outcomes.get(key) == "delivered")
    r.manual = sum(c.usd for c in costs if c.outcome == "manual")
    r.per_approved = (total - r.manual) / r.approved if r.approved else None
    if r.pace and r.per_approved is not None:
        per_day = len(conf().channel.slots)
        left = r.pace["days"] - r.pace["day"]
        r.pace.update(reels_per_day=per_day, projection=total + left * per_day * r.per_approved)

    by_outcome = _sum_by(costs, lambda c: c.outcome)
    r.outcome_usd = dict(by_outcome)
    r.outcomes = [
        (label, by_outcome.get(key, 0.0))
        for key, label in conf().costs.outcomes
        if key in ("delivered", "waste", "pending", "overhead") or by_outcome.get(key)
    ]
    status_labels = dict(conf().costs.reel_statuses)
    by_detail = _sum_by([c for c in costs if c.outcome == "waste"], lambda c: c.detail)
    r.waste = sorted(
        ((status_labels.get(d) or conf().costs.no_reel.get(d, d), usd) for d, usd in by_detail.items()),
        key=lambda x: -x[1],
    )

    # Reel runs: the spend tied to the reel itself (render, TTS, images, QA).
    runs = _sum_by([c for c in costs if c.row.reel_id is not None and not c.row.manual], lambda c: c.row.reel_id)
    r.runs = len(runs)
    r.median_run = statistics.median(runs.values()) if runs else None

    # Top reels: everything that counts under the reel.
    per_reel: dict[int, list[Cost]] = defaultdict(list)
    for c in costs:
        if counts_for_reel(c) is not None:
            per_reel[c.reel_id].append(c)
    ranked = sorted(per_reel.items(), key=lambda kv: (-sum(c.usd for c in kv[1]), kv[0]))[: conf().costs.top_n]
    for rid, items in ranked:
        reel = world.reels[rid]
        pick = world.picks.get(reel["pick_id"]) or {}
        steps = _sum_by(items, lambda c: c.step)
        big = max(steps.items(), key=lambda kv: kv[1])
        r.top.append(
            {
                "reel_id": rid,
                "date": local(reel["created_at"]).date(),
                "status": world.reel_status(rid),
                "slot": f"{pick.get('slot_date')} {pick.get('slot_time')}" if pick else None,
                "title": world.titles.get(pick.get("story_id")) or reel.get("name") or "",
                "total": sum(c.usd for c in items),
                "biggest": (step_labels[big[0]], big[1]),
            }
        )

    r.daily = sorted(_sum_by(costs, lambda c: local(c.row.at).date()).items())
    r.estimated = sum(c.usd for c in costs if c.row.estimated)
    r.unpriced = sorted({c.row.model for c in costs if c.row.usd is None and not c.row.error})
    r.upper_bound = any(c.row.purpose == "image_generate_upper_bound" for c in costs)
    failed = [c for c in costs if c.row.error]
    r.failed_tries, r.failed_usd = len(failed), sum(c.usd for c in failed)
    return r


def usd(x: float) -> str:
    return f"${x:,.2f}" if abs(x) >= 1 else f"${x:.4f}"


def share(x: float, total: float) -> str:
    return f"{x / total:.0%}" if total else "-"


def shorten(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def format_report(r: Report) -> str:
    """The report as one plain-text message (split by the sender when over 4096)."""
    prev = r.month.prev()
    delta = r.total - r.prev_total
    change = (
        f"{'+' if delta >= 0 else '-'}{usd(abs(delta))}"
        + (f" ({delta / r.prev_total:+.0%})" if r.prev_total else "")
        + f" vs {prev.short} ({usd(r.prev_total)})"
    )
    out = [f"💸 Costs: {r.month.label} (Tbilisi time)", f"Total {usd(r.total)}, {change}"]
    if r.pace:
        p = r.pace
        out.append(
            f"So far {usd(p['so_far'])} in {p['day']} of {p['days']} days; pace estimate "
            f"{usd(p['estimate'])} for the month (at the daily average so far)"
        )
        if "projection" in p:
            n = p["reels_per_day"]
            out.append(
                f"At {n} reel{'' if n == 1 else 's'} a day: {usd(p['projection'])} for the month (so far, "
                f"then {p['days'] - p['day']} days at {usd(r.per_approved)} per approved reel)"
            )
    if not r.rows:
        out += ["", "No cost rows this month."]
        return "\n".join(out + [""] + footer(r))

    out += ["", "By provider"]
    for label, x in r.providers:
        out.append(f"{label} {usd(x)} ({share(x, r.total)})")
        if label == dict((k, lab) for k, lab, _ in conf().costs.providers).get("google"):
            out += [f"  · {k} {usd(v)}" for k, v in r.google]

    out += ["", "By step"]
    out += [f"{label} {usd(x)} ({share(x, r.total)})" for label, x in r.steps]

    out += ["", "By outcome"]
    out.append("Reels: " + ", ".join(f"{n} {label}" for label, n in r.reel_counts))
    waste_label = dict(conf().costs.outcomes)["waste"]
    for label, x in r.outcomes:
        out.append(f"{label} {usd(x)} ({share(x, r.total)})")
        if label == waste_label:
            out += [f"  · {d} {usd(v)}" for d, v in r.waste]
    dev = f"; manual dev {usd(r.manual)} left out" if r.manual else ""
    out.append(
        f"Cost per approved reel: {usd(r.per_approved)} (all spend / {r.approved} approved{dev})"
        if r.per_approved is not None
        else "Cost per approved reel: - (no approved reels)"
    )
    if r.median_run is not None:
        out.append(f"Median reel run: {usd(r.median_run)} ({r.runs} run{'s' * (r.runs != 1)}: render, TTS, images, QA)")

    if r.top:
        out += ["", f"Top {len(r.top)} reels"]
        for t in r.top:
            slot = ""
            if t["slot"]:
                d, hhmm = t["slot"].split(" ")
                slot = f", slot {date.fromisoformat(d).strftime('%b %d')} {hhmm}"
            out.append(
                f"#{t['reel_id']} {t['date'].strftime('%b %d')}{slot} ({t['status']}): "
                f"{shorten(t['title'], conf().costs.title_chars)}, {usd(t['total'])}; "
                f"biggest: {t['biggest'][0]} {usd(t['biggest'][1])}"
            )

    out += ["", "Daily"]
    top = max(x for _, x in r.daily) or 1.0
    for d, x in r.daily:
        bar = "█" * max(1, round(x / top * conf().costs.daily_bar_chars)) if x > 0 else ""
        out.append(f"{d.strftime('%b %d')} {bar} {usd(x)}")
    return "\n".join(out + [""] + footer(r))


def footer(r: Report) -> list[str]:
    out = []
    if r.estimated:
        out.append(f"Includes {usd(r.estimated)} estimated (rows backfilled in migration 11).")
    if r.upper_bound:
        out.append("Image generation includes an upper-bound price (no modality breakdown).")
    if r.failed_tries:
        n = r.failed_tries
        out.append(
            f"🔁 Retries: {n} {'try' if n == 1 else 'tries'} dropped and tried again, {usd(r.failed_usd)} "
            "(the tokens known when each dropped: a lower bound)"
        )
    if r.unpriced:
        out.append(f"⚠️ Calls with no price, counted as $0: {', '.join(r.unpriced)}")
    out.append(conf().costs.footer)
    if conf().costs.untracked:
        out.append("Not tracked: " + "; ".join(conf().costs.untracked))
    return out


def csv_text(costs: list[Cost], world: World) -> str:
    """Every cost row, costs.csv_columns."""
    steps = {key: label for key, label, _ in conf().costs.steps}
    providers = {key: label for key, label, _ in conf().costs.providers} | {"other": conf().costs.other_provider}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(conf().costs.csv_columns)
    for c in costs:
        r = c.row
        pick = world.picks.get(c.pick_id) or {}
        values = {
            "timestamp": r.at,
            "timestamp_tbilisi": local(r.at).isoformat(timespec="seconds"),
            "provider": providers[c.provider],
            "model": r.model,
            "step": steps[c.step],
            "purpose": r.purpose,
            "pick": c.pick_id,
            "slot": f"{pick['slot_date']} {pick['slot_time']}" if pick else None,
            "reel": c.reel_id,
            "script": r.script_id,
            "job": r.job_id,
            "outcome": f"{c.outcome} ({c.detail})" if c.detail != c.outcome else c.outcome,
            "manual": int(r.manual),
            "estimated": r.estimated,
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "tokens_thinking": r.tokens_thinking,
            "usd": None if r.usd is None else f"{r.usd:.6f}",
        }
        w.writerow(["" if values[k] is None else values[k] for k in conf().costs.csv_columns])
    return buf.getvalue()
