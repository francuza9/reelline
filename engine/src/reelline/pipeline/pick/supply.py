"""The supply check: how many stories pass the rules at every slot (dry)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.pick.base import iso_ts, opt_bool
from reelline.pipeline.pick.picker import Picker
from reelline.pipeline.scoring import (
    Candidate,
    dedupe,
    evaluate,
    rank,
    slot_datetime,
    tbilisi,
)
from reelline.pipeline.sources import load_sources


def supply(picker: Picker, days: int, enabled: set[str], log: Callable[[str], None] = print) -> dict:
    """The supply check (module docstring): {"slots": [...], "stories": [...], "summary": {...}}."""
    from collections import Counter, defaultdict

    from reelline.pipeline.assess import assess_dry, story_input

    conn, now = picker.conn, picker.now()
    since = now - timedelta(days=days)
    rows = [
        r
        for r in conn.execute(
            "SELECT * FROM stories WHERE fetched_at >= ? AND status != 'archived' ORDER BY id", (db.now_iso(since),)
        )
        if r["source_id"] in enabled
    ]
    start_h, start_m = (int(x) for x in conf().steps.run_daily.evening_start_at.split(":"))
    slot = conf().channel.slots[0]

    def first_slot(row) -> date:
        seen = iso_ts(row["fetched_at"]).astimezone(tbilisi())
        early = (seen.hour, seen.minute) < (start_h, start_m)
        return seen.date() + timedelta(days=1 if early else 2)

    groups: dict[date, list] = defaultdict(list)
    for r in rows:
        groups[first_slot(r)].append(r)
    fields: dict[int, dict] = {}
    client = picker.client_factory()
    with db.cost_context(manual=True):
        for day in sorted(groups):
            inputs = [story_input(r, picker.source_names.get(r["source_id"], r["source_id"])) for r in groups[day]]
            for i in range(0, len(inputs), conf().steps.pick.assess_batch):
                got, _ = assess_dry(
                    conn, client, inputs[i : i + conf().steps.pick.assess_batch], day - timedelta(days=1), day, log=log
                )
                fields.update(got)
            log(f"  {day.isoformat()}: {len(inputs)} assessed")
    cands, first = [], {}
    for r in rows:
        f = fields.get(r["id"])
        if f is None:  # the model never assessed it (assess_dry): left out of the check
            continue
        cands.append(
            Candidate(
                story_id=r["id"],
                title=r["title"],
                source_id=r["source_id"],
                canonical_url=r["canonical_url"],
                status="new",
                published_at=iso_ts(r["published_at"]),
                date_source=r["date_source"],
                fetched_at=iso_ts(r["fetched_at"]),
                topic=f["topic"],
                banned=bool(f["banned"]),
                banned_reason=f.get("banned_reason"),
                base_score=f["base_score"],
                same_program_key=f["same_program_key"],
                summary=f["summary"],
                edu_type=f.get("edu_type"),
                provider=f.get("provider"),
                provider_tier=f.get("provider_tier"),
                cost=f.get("cost"),
                marketing_or_low_credibility=opt_bool(f.get("marketing_or_low_credibility")),
                georgia_relevance=f.get("georgia_relevance"),
                deadline=date.fromisoformat(f["deadline"]) if f.get("deadline") else None,
                who_can_apply=f.get("who_can_apply"),
                prompt_version=conf().steps.pick.assess_prompt_version,
                opportunity=opt_bool(f.get("opportunity")),
                open_status=f.get("open_status"),
                reach=f.get("reach"),
                novelty=f.get("novelty"),
                event_date=date.fromisoformat(f["event_date"]) if f.get("event_date") else None,
                hook_strength=f.get("hook_strength"),
            )
        )
        first[r["id"]] = first_slot(r)
    today = now.astimezone(tbilisi()).date()
    out_slots: list = []
    eligible_once: dict = {}
    day = min(first.values(), default=today)
    while day <= today + timedelta(days=1):
        slot_dt = slot_datetime(day, slot)
        pool = [c for c in cands if first[c.story_id] <= day]
        ranked, _ = dedupe(rank([evaluate(c, slot_dt, [], set(), allow_tech=False) for c in pool]))
        for e in ranked:
            eligible_once.setdefault(e.candidate.story_id, day)
        out_slots.append(
            {
                "slot": f"{day.isoformat()} {slot}",
                "pool": len(pool),
                "eligible": len(ranked),
                "top": [
                    {"story_id": e.candidate.story_id, "effective": e.effective, "summary": e.candidate.summary}
                    for e in ranked[:3]
                ],
            }
        )
        day += timedelta(days=1)
    stories: list = []
    reasons: Counter[str] = Counter()
    for c in cands:
        e = evaluate(c, slot_datetime(first[c.story_id], slot), [], set(), allow_tech=False)
        if not e.eligible:
            reasons[re.sub(r"\s*\(.*", "", e.gates[0]).split(":")[0]] += 1
        stories.append(
            {
                "story_id": c.story_id,
                "source": c.source_id,
                "topic": c.topic,
                "summary": c.summary,
                "first_slot": first[c.story_id].isoformat(),
                "eligible_from": (eligible_once[c.story_id].isoformat() if c.story_id in eligible_once else None),
                "effective": e.effective,
                "gates": e.gates,
                "parts": e.parts,
                "fields": {k: v for k, v in fields[c.story_id].items() if v is not None},
            }
        )
    span = max(1, len(out_slots))  # the slots checked, one a day
    distinct = len(eligible_once)
    summary = {
        "days": days,
        "stories": len(cands),
        "sources": sorted(enabled),
        "distinct_eligible": distinct,
        "per_day": round(distinct / span, 2),
        "span_days": span,
        "slots_without_eligible": [x["slot"] for x in out_slots if not x["eligible"]],
        "reasons": reasons.most_common(),
    }
    return {"slots": out_slots, "stories": stories, "summary": summary}


def supply_text(report: dict) -> str:
    sm = report["summary"]
    lines = [
        f"Supply check: {sm['stories']} stories from {len(sm['sources'])} enabled sources, last {sm['days']} "
        f"days. {sm['distinct_eligible']} distinct eligible {conf().steps.pick.topic} opportunities over {sm['span_days']} days "
        f"= {sm['per_day']} a day."
    ]
    lines.append(
        f"Slots with nothing eligible: {len(sm['slots_without_eligible'])}"
        + (f" ({', '.join(sm['slots_without_eligible'])})" if sm["slots_without_eligible"] else "")
    )
    lines += ["", "Per slot (eligible / pool, then the top one):"]
    for x in report["slots"]:
        top = x["top"][0] if x["top"] else None
        lines.append(
            f"  {x['slot']}: {x['eligible']} / {x['pool']}"
            + (f"  #{top['story_id']} {top['effective']:.2f} {top['summary'][:70]}" if top else "")
        )
    lines += ["", "Why the rest are out (first gate, at their first slot):"]
    lines += [f"  {why}: {n}" for why, n in sm["reasons"]]
    lines += ["", "Eligible:"]
    for st in sorted((x for x in report["stories"] if x["eligible_from"]), key=lambda x: x["eligible_from"]):
        lines.append(f"  {st['eligible_from']} #{st['story_id']} [{st['source']}] {st['summary'][:90]}")
    return "\n".join(lines)


def cmd_supply(args, picker: Picker) -> int:
    from reelline.paths import REPO_ROOT

    enabled = {src.id for src in load_sources(args.registry) if src.enabled and src.assess}
    report = supply(picker, args.days, enabled)
    out = REPO_ROOT / "out" / "supply" / f"supply_{picker.now().strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(supply_text(report))
    print(f"\nDetails: {out}")
    return 0
