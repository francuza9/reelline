"""The Picker: ranking a slot's stories and picking (queued, menu or auto)."""

from __future__ import annotations

from datetime import date

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.assess import assess_stories
from reelline.pipeline.pick.base import PickError, Ranking, assess_cost, nothing_eligible_text
from reelline.pipeline.pick.loading import load_candidates, load_dismissed, load_history
from reelline.pipeline.pick.menus import PickerMenus
from reelline.pipeline.scoring import (
    contenders,
    dedupe,
    evaluate,
    rank,
    slot_datetime,
    tech_slot,
    weekly_topic,
)
from reelline.pipeline.techpick import assess_tech


class Picker(PickerMenus):
    def ranking(self, slot_day: date, slot_time: str) -> Ranking:
        """Eligible stories for the slot, best first; a story the reviewer dropped from
        this slot (its pick rejected or skipped) isn't offered for it again, and a
        dismissed one (or its twin) for no slot."""
        slot_dt = slot_datetime(slot_day, slot_time)
        history = load_history(self.conn)
        posted = {p.story_id for p in history if p.status == "posted"}
        dropped = {
            r[0]
            for r in self.conn.execute(
                "SELECT story_id FROM picks WHERE slot_date = ? AND slot_time = ? AND status IN ('rejected', 'skipped')",
                (slot_day.isoformat(), slot_time),
            )
        }
        dismissed = load_dismissed(self.conn)
        evaluations = [
            evaluate(c, slot_dt, history, posted, dismissed=dismissed)
            for c in load_candidates(self.conn)
            if c.story_id not in dropped
        ]
        unique, _ = dedupe(rank(evaluations))
        if tech_slot(slot_dt):
            # The weekly big-tech slot: the best tech story if one is eligible (it cleared
            # the score threshold), else education as on any day.
            tech = [e for e in unique if e.candidate.topic == weekly_topic()]
            unique = tech or [e for e in unique if e.candidate.topic != weekly_topic()]
        return Ranking(slot_dt, evaluations, unique)

    def run(self, slot_day: date, slot_time: str, dry_run: bool = False, menu: bool = True) -> int:
        """Pick for one slot. menu False (an evening-batch slot, reelline/pipeline/run_daily.py): close
        calls are decided automatically too (#1, no Telegram menu)."""
        if slot_time not in conf().channel.slots:
            raise PickError(f"Unknown slot {slot_time}; slots are {', '.join(conf().channel.slots)}")
        slot_key = (slot_day.isoformat(), slot_time)
        label = f"{slot_key[0]} {slot_time}"
        slot_dt = slot_datetime(slot_day, slot_time)
        if slot_dt < self.now():
            self.log(f"note: slot {label} is in the past")
        existing = db.slot_pick(self.conn, *slot_key)
        if existing is not None:
            self.log(
                f"Slot {label} already has pick #{existing['id']} (story {existing['story_id']}, "
                f"{existing['status']}, by {existing['chosen_by']}). Nothing to do."
            )
            return 0
        if not dry_run:
            self.expire()
        pending = [m for m in db.open_menus(self.conn) if (m["slot_date"], m["slot_time"]) == slot_key]
        if pending:
            self.log(
                f"Menu {pending[0]['id']} for {label} is still open (until {pending[0]['deadline_at']}); "
                f"answer it in Telegram (the approval bot), or with `choose`."
            )
            return 0

        unassessed = len(db.unassessed_story_ids(self.conn))
        if unassessed and dry_run:
            self.log(f"dry run: {unassessed} stories not assessed yet are left out (a real run assesses them)")
        elif unassessed:
            self.log(f"Assessing {unassessed} new stories...")
            totals = assess_stories(
                self.conn, self.client_factory(), self.source_names, publish_date=slot_day, log=self.log
            )
            self.log(f"  {totals['input']} input + {totals['output']} output tokens, about ${assess_cost(totals):.4f}")

        tech_day = tech_slot(slot_dt)
        if tech_day and not dry_run:
            # The weekly big-tech slot: a capped, pre-filtered assessment of archived tech stories.
            totals = assess_tech(
                self.conn, self.client_factory(), self.source_names, slot_day, self.now(), log=self.log
            )
            if totals:
                self.log(
                    f"  {totals['input']} input + {totals['output']} output tokens, about ${assess_cost(totals):.4f}"
                )
        tech_first = tech_day and any(
            e.candidate.topic == weekly_topic() for e in self.ranking(slot_day, slot_time).ranked
        )

        # Queued stories first, in order, while they're still eligible.
        history = load_history(self.conn)
        posted = {p.story_id for p in history if p.status == "posted"}
        dismissed = load_dismissed(self.conn)
        for queued in db.queued_picks(self.conn):
            (cand,) = load_candidates(self.conn, [queued["story_id"]])
            if tech_first and cand.topic != weekly_topic():
                self.log(
                    f"Queued pick #{queued['id']} waits: {label} is the {weekly_topic()} slot and a {weekly_topic()} story is eligible"
                )
                continue
            ev = evaluate(cand, slot_dt, history, posted, expect_status="queued", dismissed=dismissed)
            if ev.eligible:
                self.log(f"Queued pick #{queued['id']} goes to {label}: {self.describe(ev)}")
                if dry_run:
                    return 0
                db.update_pick(
                    self.conn,
                    queued["id"],
                    story_status="picked",
                    status="picked",
                    slot_date=slot_key[0],
                    slot_time=slot_time,
                    effective_score=ev.effective,
                )
                self.on_picked(queued["id"])
                return 0
            reason = "queued, no longer eligible: " + "; ".join(ev.gates)
            self.log(f"Queued pick #{queued['id']} (story {cand.story_id}) dropped: {'; '.join(ev.gates)}")
            if not dry_run:
                db.update_pick(
                    self.conn,
                    queued["id"],
                    story_status="rejected",
                    story_reason=reason,
                    status="rejected",
                    note=reason,
                )

        r = self.ranking(slot_day, slot_time)
        if r.note:
            self.log(r.note)
        if not r.ranked:
            message = nothing_eligible_text(label, r.evaluations)
            self.log(message)
            if not dry_run:
                self.bot.send(message)
            return 1
        self.log(f"Top of {len(r.ranked)} eligible (of {len(r.evaluations)} assessed):")
        for e in r.ranked[: conf().steps.pick.menu_size]:
            self.log(f"  {self.describe(e)}")

        close = contenders(r.ranked)
        if len(close) >= 2 and not menu:
            self.log(
                f"{len(close)} contenders within {conf().steps.pick.contender_within:g} of the top: no menu for this slot "
                "(evening batch), #1 is picked"
            )
        elif len(close) >= 2:
            self.log(
                f"{len(close)} contenders within {conf().steps.pick.contender_within:g} of the top "
                f"(>= {conf().steps.pick.contender_min:g}): a menu for a human choice"
            )
            if dry_run:
                self.log(
                    "dry run: the menu would be:\n"
                    + self.menu_text(self.snapshot(r.ranked), label, self.now() + self._menu_timeout())
                )
                return 0
            self.open_menu(slot_key, r.ranked)
            return 0

        top = r.ranked[0]
        self.log(f"Auto-pick for {label}: {self.describe(top)}")
        if dry_run:
            return 0
        pick_id = db.add_pick(
            self.conn, top.candidate.story_id, "picked", "auto", self.snapshot(r.ranked), top.effective, *slot_key
        )
        self.on_picked(pick_id)
        return 0
