"""The list and queue commands: the next slot's ranking and the queued picks."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.pick.loading import load_candidates, load_dismissed, load_history
from reelline.pipeline.pick.picker import Picker
from reelline.pipeline.scoring import (
    contenders,
    evaluate,
    slot_datetime,
    tbilisi,
)


def next_slot(now: datetime) -> tuple[date, str]:
    """The first slot after `now` (Tbilisi time)."""
    local = now.astimezone(tbilisi())
    for day in (local.date(), local.date() + timedelta(days=1)):
        for slot in sorted(conf().channel.slots):
            if slot_datetime(day, slot) > now:
                return day, slot
    raise AssertionError("unreachable")


def cmd_list(args, picker: Picker) -> int:
    day, slot = next_slot(picker.now())
    r = picker.ranking(day, slot)

    def wanted(e) -> bool:
        return (not args.topic or e.candidate.topic == args.topic) and (
            not args.source or e.candidate.source_id in args.source
        )

    evaluations = [e for e in r.evaluations if wanted(e)]
    ranked = [e for e in r.ranked if wanted(e)]
    unassessed = len(db.unassessed_story_ids(picker.conn))
    print(
        f"Ranking for {day.isoformat()} {slot}: {len(ranked)} eligible of {len(evaluations)} assessed"
        + (f" ({unassessed} not assessed yet)" if unassessed else "")
    )
    if r.note:
        print(r.note)
    close = {e.candidate.story_id for e in contenders(r.ranked)}
    for n, e in enumerate(ranked, 1):
        mark = "*" if e.candidate.story_id in close and len(close) >= 2 else " "
        print(
            f"{n:>3}{mark} {picker.describe(e)}\n       base {e.candidate.base_score:g}, "
            f"effective {e.effective:.2f}: {'; '.join(e.parts)}"
        )
    if len(close) >= 2:
        print(f"  * {len(close)} contenders: a run now would send a menu")
    if args.source:
        print("\nPer source (eligible / assessed):")
        for source in args.source:
            mine = [e for e in evaluations if e.candidate.source_id == source]
            print(f"  {source}: {sum(e.eligible for e in mine)} / {len(mine)}")
    if args.all:
        out = sorted((e for e in evaluations if not e.eligible), key=lambda e: (e.candidate.topic, -(e.effective or 0)))
        print(f"\nNot eligible ({len(out)}):")
        for e in out:
            score = f"{e.effective:.2f}" if e.effective is not None else "-"
            print(
                f"  #{e.candidate.story_id} [{e.candidate.topic} base {e.candidate.base_score:g} eff {score}] "
                f"{e.candidate.summary[:90]}\n       OUT: {'; '.join(e.gates)}"
            )
    return 0


def cmd_queue(args, picker: Picker) -> int:
    queued = db.queued_picks(picker.conn)
    if not queued:
        print("Queue is empty.")
        return 0
    day, slot = next_slot(picker.now())
    slot_dt = slot_datetime(day, slot)
    history = load_history(picker.conn)
    posted = {p.story_id for p in history if p.status == "posted"}
    dismissed = load_dismissed(picker.conn)
    print(f"Queued picks, checked against {day.isoformat()} {slot}:")
    for q in queued:
        (cand,) = load_candidates(picker.conn, [q["story_id"]])
        ev = evaluate(cand, slot_dt, history, posted, expect_status="queued", dismissed=dismissed)
        state = "eligible" if ev.eligible else "OUT: " + "; ".join(ev.gates)
        print(
            f"  {q['queue_position']}. pick #{q['id']} story #{q['story_id']} [{cand.topic} "
            f"{ev.effective if ev.effective is not None else '-'}] {cand.summary}\n       {state} "
            f"(queued {q['created_at'][:16]}, menu {q['menu_id']})"
        )
    return 0
