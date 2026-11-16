"""The flirt line: the previous opening emotion, the prompt's flirt block, the per-pick roll."""

from __future__ import annotations

import sqlite3

from reelline.config import conf
from reelline.media import style as reel_style
from reelline.media.prompts import fill
from reelline.media.script_markup import (
    MarkupError,
    parse,
)
from reelline.pipeline import db
from reelline.pipeline.script.base import NO_RECENT_FLIRTS, ScriptError


def previous_opening(conn: sqlite3.Connection, script_id: int | None = None) -> str | None:
    """The opening emotion of the latest script before `script_id` (all scripts when None)
    that has one, among the steps.script.opening_lookback latest with a final version that
    isn't failed: plain openers don't count. None if none of them has one."""
    rows = conn.execute(
        """SELECT final FROM scripts WHERE final IS NOT NULL AND status != 'failed' AND id < ?
                           ORDER BY id DESC LIMIT ?""",
        (script_id if script_id is not None else 1 << 62, conf().steps.script.opening_lookback),
    ).fetchall()
    for row in rows:
        try:
            opening = parse(row["final"]).opening
        except MarkupError:
            continue
        if opening is not None:
            return opening
    return None


def flirt_block(
    flirt: bool, recent: str = NO_RECENT_FLIRTS, *, angle: str | None = None, banned_ok: bool = True
) -> str:
    """{{FLIRT}}: the flirt line's instructions (its angle, the recent ones not to repeat,
    and the banned-phrase rule unless `banned_ok`), or the ban on flirting."""
    if not flirt:
        return conf().steps.script.flirt_off_prompt.read_text(encoding="utf-8").strip()
    if angle is None:
        raise ScriptError("A flirt line needs its angle (picks.flirt_angle)")
    block = fill(
        conf().steps.script.flirt_on_prompt.read_text(encoding="utf-8"),
        {"ANGLE": angle, "RECENT_FLIRTS": recent},
        conf().steps.script.flirt_on_prompt.name,
    ).strip()
    if not banned_ok:
        block += "\n" + conf().steps.script.flirt_banned_prompt.read_text(encoding="utf-8").strip()
    return block


def has_banned_phrase(line: str | None) -> bool:
    """Whether a flirt line uses a banned phrase: a word with one of flirt_banned_stems."""
    return bool(line) and any(
        stem in word for word in line.casefold().split() for stem in conf().steps.script.flirt_banned_stems
    )


def pick_flirt(conn: sqlite3.Connection, pick: sqlite3.Row, roll=None, choose=None) -> tuple[bool, str | None]:
    """Whether the pick's reel gets a flirt line, and its angle (None without one): the
    stored values; else an earlier pick's for the same slot (Other story keeps them); else
    rolled now (FLIRT_PROBABILITY, one of FLIRT_ANGLES) and stored. A flirt line without an
    angle (rolled before the angles) gets one now. A style whose flirt_probability is 0
    (reelline/media/style.py straight) gives none, a stored yes included, and stores the no (the review
    reads picks.flirt)."""
    import random

    probability = reel_style.current().flirt_probability
    flirt, angle = pick["flirt"], pick["flirt_angle"]
    if probability == 0:  # a style without the flirt line (straight): none, a stored yes too
        flirt = 0
    if flirt is None and pick["slot_date"]:
        earlier = conn.execute(
            """SELECT flirt, flirt_angle FROM picks WHERE slot_date = ? AND slot_time = ?
                                  AND id != ? AND flirt IS NOT NULL ORDER BY id DESC LIMIT 1""",
            (pick["slot_date"], pick["slot_time"], pick["id"]),
        ).fetchone()
        if earlier is not None:
            flirt, angle = earlier["flirt"], earlier["flirt_angle"]
    if flirt is None:
        flirt = int(
            (roll or random.random)() < (conf().steps.script.flirt_probability if probability is None else probability)
        )
    if not flirt:
        angle = None
    elif angle is None:
        angle = (choose or random.choice)(conf().steps.script.flirt_angles)
    if (flirt, angle) != (pick["flirt"], pick["flirt_angle"]):
        with db.transaction(conn):
            conn.execute("UPDATE picks SET flirt = ?, flirt_angle = ? WHERE id = ?", (int(flirt), angle, pick["id"]))
    return bool(flirt), angle
