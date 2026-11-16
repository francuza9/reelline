"""The Picker's menus: open, answer, dismiss, expire, Telegram taps."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

from reelline.config import conf
from reelline.notify.base import buttons
from reelline.notify.text import framed
from reelline.pipeline import db
from reelline.pipeline.pick.base import PickError, iso_ts
from reelline.pipeline.pick.state import PickerState
from reelline.pipeline.scoring import (
    Evaluation,
    tbilisi,
)
from reelline.review.base import Tap


class PickerMenus(PickerState):
    @staticmethod
    def _menu_timeout() -> timedelta:
        return timedelta(minutes=conf().steps.pick.menu_timeout_min)

    def menu_text(self, candidates: list[dict], label: str, deadline: datetime) -> str:
        local = deadline.astimezone(tbilisi()).strftime("%H:%M")
        lines = [
            f"{conf().channel.shown_name()} pick for {label}: a close call. Tap one or more, then Confirm. The first goes to "
            f"this slot, the rest are queued for the next ones; the ones not tapped are dismissed for good. "
            f"None of these: all dismissed, the slot takes the best story outside the menu. "
            f"No answer by {local}: #1 is picked, nothing dismissed."
        ]
        for n, c in enumerate(candidates, 1):
            extra = f" | deadline {c['deadline']}" if c["deadline"] else ""
            lines.append(
                f"\n{n}. [{c['topic']} {c['effective']:.2f}{self.hook_text(c.get('hook'))}] {c['summary']}\n   {c['source']}{extra}"
            )
        return "\n".join(lines)

    @staticmethod
    def menu_markup(menu_id: int, count: int, selected: list[int]) -> list:
        def label(n: int) -> str:
            return f"{n} ✓{selected.index(n) + 1}" if n in selected else str(n)

        return buttons(
            [
                [(label(n), f"pick:{menu_id}:t:{n}") for n in range(1, count + 1)],
                [("Confirm", f"pick:{menu_id}:ok"), ("None of these", f"pick:{menu_id}:none")],
            ]
        )

    def open_menu(self, slot_key: tuple[str, str], ranked: list[Evaluation]) -> int:
        candidates = self.snapshot(ranked)
        deadline = self.now() + self._menu_timeout()
        menu_id = db.add_menu(self.conn, *slot_key, candidates, db.now_iso(deadline))
        self.bot.send(framed(conf().review.telegram.header["menu"].format(date=slot_key[0], slot=slot_key[1])))
        ref = self.bot.send(
            self.menu_text(candidates, " ".join(slot_key), deadline), self.menu_markup(menu_id, len(candidates), [])
        )
        db.update_menu(self.conn, menu_id, message_id=ref)
        self.log(
            f"Menu {menu_id} sent to the review channel; the approval bot (python -m reelline.pipeline.approve_bot) takes the "
            f"answer (or `choose {menu_id} <n...>`)"
        )
        return menu_id

    def _open_menu_row(self, menu_id: int) -> sqlite3.Row:
        menu = db.get_menu(self.conn, menu_id)
        if menu is None:
            raise PickError(f"No menu {menu_id}")
        if menu["status"] != "open":
            raise PickError(f"Menu {menu_id} is already {menu['status']}")
        return menu

    def _dismiss(
        self, menu: sqlite3.Row, positions: list[int], reason: str, at: str, note: str | None = None
    ) -> list[tuple[int, int, str]]:
        """Dismiss the menu's contenders at these positions whose story is still "new"
        (inside the caller's transaction): [(position, story id, title)] dismissed."""
        candidates = json.loads(menu["candidates"])
        done = []
        for n in positions:
            story = db.get_story(self.conn, candidates[n - 1]["story_id"])
            if story is None or story["status"] != "new":
                self.log(
                    f"Menu {menu['id']}: #{n} (story {candidates[n - 1]['story_id']}) not dismissed: "
                    f"{'missing' if story is None else story['status']}"
                )
                continue
            db.dismiss_story(
                self.conn, story["id"], reason, menu["id"], menu["slot_date"], menu["slot_time"], at=at, note=note
            )
            done.append((n, story["id"], story["title"]))
        return done

    @staticmethod
    def dismissed_lines(dismissed: list[tuple[int, int, str]]) -> str:
        """'Dismissed: #3, #4' and a line per story with its title."""
        if not dismissed:
            return ""
        return (
            "\nDismissed: "
            + ", ".join(f"#{n}" for n, _, _ in dismissed)
            + "".join(f"\n  #{n} {title[:80]}" for n, _, title in dismissed)
        )

    def _edit_menu(self, menu: sqlite3.Row, outcome: str) -> None:
        if menu["message_id"] is not None:
            deadline = iso_ts(menu["deadline_at"])
            text = self.menu_text(json.loads(menu["candidates"]), f"{menu['slot_date']} {menu['slot_time']}", deadline)
            self.bot.edit(menu["message_id"], f"{text}\n\n{outcome}")

    def resolve(self, menu_id: int, positions: list[int], by: str) -> int:
        """Answer a menu: positions (1-based, in chosen order) -> the first is picked for
        the menu's slot, the rest queued; by human, every other contender is dismissed.
        by: human | timeout (nothing dismissed). Returns the pick id."""
        menu = self._open_menu_row(menu_id)
        candidates = json.loads(menu["candidates"])
        if not positions:
            raise PickError("Choose at least one")
        bad = [n for n in positions if not 1 <= n <= len(candidates)]
        if bad or len(set(positions)) != len(positions):
            raise PickError(f"Choices must be distinct numbers 1-{len(candidates)}, got {positions}")
        chosen = [candidates[n - 1] for n in positions]
        with db.transaction(self.conn):
            pick_id = db.add_pick(
                self.conn,
                chosen[0]["story_id"],
                "picked",
                by,
                candidates,
                chosen[0]["effective"],
                menu["slot_date"],
                menu["slot_time"],
                menu_id=menu_id,
            )
            position = db.next_queue_position(self.conn)
            for i, c in enumerate(chosen[1:]):
                db.add_pick(
                    self.conn,
                    c["story_id"],
                    "queued",
                    "human",
                    candidates,
                    c["effective"],
                    menu_id=menu_id,
                    queue_position=position + i,
                )
            at = db.now_iso(self.now())
            dismissed = []
            if by == "human":
                unchosen = [n for n in range(1, len(candidates) + 1) if n not in positions]
                dismissed = self._dismiss(menu, unchosen, "menu_unselected", at)
            db.update_menu(
                self.conn,
                menu_id,
                status="resolved" if by == "human" else "timeout",
                selected=json.dumps(positions),
                resolved_at=at,
                resolved_by=by,
            )
        if by == "timeout":
            outcome = f"No answer in time: auto-picked #1 for {menu['slot_time']}; the others stay eligible."
        else:
            outcome = f"Chosen: #{positions[0]} for {menu['slot_time']}"
            outcome += f"; queued: {', '.join(f'#{n}' for n in positions[1:])}." if positions[1:] else "."
            outcome += self.dismissed_lines(dismissed)
        self.log(f"Menu {menu_id}: {outcome}")
        self._edit_menu(menu, outcome)
        self.on_picked(pick_id)
        return pick_id

    def none_of_these(self, menu_id: int, by: str = "human") -> list[int]:
        """ "None of these": every contender dismissed (menu_none), the menu closed (status
        none), then the slot picked outside the menu (on_none, else run() here with no
        menu). Returns the dismissed story ids."""
        menu = self._open_menu_row(menu_id)
        count = len(json.loads(menu["candidates"]))
        with db.transaction(self.conn):
            at = db.now_iso(self.now())
            dismissed = self._dismiss(menu, list(range(1, count + 1)), "menu_none", at)
            db.update_menu(self.conn, menu_id, status="none", selected="[]", resolved_at=at, resolved_by=by)
            if self.on_none is not None:
                self.on_none(menu)
        outcome = (
            f"None of these: the {menu['slot_time']} slot takes the best eligible story outside this menu."
            + self.dismissed_lines(dismissed)
        )
        self.log(f"Menu {menu_id}: {outcome}")
        self._edit_menu(menu, outcome)
        if self.on_none is None:
            self.run(date.fromisoformat(menu["slot_date"]), menu["slot_time"], menu=False)
        return [sid for _, sid, _ in dismissed]

    def dismiss_unselected(self, menu_id: int) -> list[str]:
        """Backfill: a menu answered (Confirm) before dismissals existed gets its unchosen
        contenders dismissed as Confirm would have (menu_unselected, at the menu's answer
        time). Only stories still "new"; picks, the queue and the menu row are untouched.
        Returns a report line per contender."""
        menu = db.get_menu(self.conn, menu_id)
        if menu is None:
            raise PickError(f"No menu {menu_id}")
        if menu["status"] != "resolved" or menu["resolved_by"] != "human":
            raise PickError(
                f"Menu {menu_id} is {menu['status']} (by {menu['resolved_by']}): only a menu answered "
                f"with Confirm has unselected contenders to dismiss"
            )
        candidates = json.loads(menu["candidates"])
        selected = json.loads(menu["selected"])
        unchosen = [n for n in range(1, len(candidates) + 1) if n not in selected]
        with db.transaction(self.conn):
            done = self._dismiss(
                menu, unchosen, "menu_unselected", menu["resolved_at"], note=f"backfilled {db.now_iso(self.now())}"
            )
        dismissed = {n for n, _, _ in done}
        lines = []
        for n, c in enumerate(candidates, 1):
            story = db.get_story(self.conn, c["story_id"])
            state = "chosen, left as it is" if n in selected else "dismissed" if n in dismissed else "not dismissed"
            lines.append(f"#{n} story {c['story_id']} ({story['status']}): {state} - {story['title'][:80]}")
        return lines

    def expire(self) -> list[int]:
        """Resolve every open menu past its deadline with its #1 (chosen_by timeout)."""
        done = []
        for menu in db.open_menus(self.conn):
            if iso_ts(menu["deadline_at"]) <= self.now():
                self.resolve(menu["id"], [1], "timeout")
                done.append(menu["id"])
        return done

    def handle_update(self, update) -> None:
        """A raw update of the review channel: its menu taps handled."""
        for event in self.bot.parse(update):
            if isinstance(event, Tap):
                self.handle_tap(event)

    def handle_tap(self, query: Tap) -> None:
        if not query.data.startswith("pick:"):
            return
        parts = query.data.split(":")
        try:
            menu_id = int(parts[1])
        except (IndexError, ValueError):
            self.bot.ack(query, "Unknown button")
            return
        menu = db.get_menu(self.conn, menu_id)
        if menu is None or menu["status"] != "open":
            self.bot.ack(query, "This menu is closed")
            return
        selected = json.loads(menu["selected"])
        count = len(json.loads(menu["candidates"]))
        if parts[2:3] == ["t"] and len(parts) == 4 and parts[3].isdigit() and 1 <= int(parts[3]) <= count:
            n = int(parts[3])
            selected = [x for x in selected if x != n] if n in selected else selected + [n]
            db.update_menu(self.conn, menu_id, selected=json.dumps(selected))
            self.bot.edit_buttons(menu["message_id"], self.menu_markup(menu_id, count, selected))
            self.bot.ack(query, "Chosen: " + (", ".join(f"#{x}" for x in selected) or "none"))
        elif parts[2:3] == ["ok"]:
            if not selected:
                self.bot.ack(query, "Tap at least one story first")
                return
            self.bot.ack(query, "Done")
            self.resolve(menu_id, selected, "human")
        elif parts[2:3] == ["none"]:
            self.bot.ack(query, "All dismissed; picking outside the menu")
            self.none_of_these(menu_id)
        else:
            self.bot.ack(query, "Unknown button")
