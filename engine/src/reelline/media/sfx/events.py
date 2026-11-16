"""Per-reel SFX events: when each sound plays, which one, and at what gain.

plan() builds the event list from data the render already has:

- TV click at every wall-TV content change (image to image, idle to image, image to
  idle), from the same screenImages the Remotion screen draws (remotion/src/Scene.tsx
  imageOpacity): an image's start, and its end unless the next image starts there.
  No click for the TV's opening state (changes before TV_CLICK["opening_s"]), nor
  for the change to idle at the outro's start (or the reel's end). The click starts
  pre_offset_s before the change's first drawn frame. A change less than min_gap_s
  after the last clicked one gets no click. A click that would overlap speech (any
  aligned word) plays under_speech_db quieter instead of being skipped.
- TV power (TV_POWER, one "tv_power" sound per reel for both): "tv_on" replaces the
  click of the first change (the TV's first image), at the opening window's end if
  that change is inside it; "tv_off" at the outro start (the end card opening), on the
  frame the screen goes idle (the last image holds until then; render_reel.py
  screen_starts). Each can be turned off; both follow the click rules for timing and
  speech (the card opens just before the host's last line, so tv_off plays lowered under it).
- Fact graphics (FX_POP): a TV click as each card springs out of the TV (its start plus
  offset_s; remotion/src/Fx.tsx), none as it flies back; skipped within min_gap_s of
  another click, lowered under speech like the others.
- Outro sting once at the outro hook point: the outro's first frame (where
  remotion/src/Reel.tsx starts its Sequence) plus STING["offset_s"]. It never
  overlaps the voice under the end card (from the card's start to the tag line's end) or
  "tv_off": if it would, it moves to their end (gap_after_off_s after tv_off), so it
  plays in the card's hold after the voice. check_outro_fit() checks the config lets the
  longest allowed sting fit in the hold (remotion/src/outro.json holdS).

Variants are picked seeded by the reel, with no immediate repeat of a sound within
the reel (when the category has more than one). An empty category fails.
Gains are relative to the voice (reelline/media/sfx/config.py CATEGORIES gain_db), plus the
render's global offset (--sfx-gain).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from reelline.media.sfx.config import CATEGORIES, FX_POP, STING, TV_CLICK, TV_POWER
from reelline.media.sfx.library import SfxError, Sound, by_category


@dataclass(frozen=True)
class Event:
    event: str  # "tv_click" | "tv_on" | "tv_off" | "fx_pop" | "sting"
    time: float  # s, where the sound starts
    sound: Sound
    gain_db: float  # relative to the voice, in the sound's category measure
    note: str | None = None  # why the event was lowered or moved

    def row(self) -> dict:
        row = {"event": self.event, "time": self.time, "sound": self.sound.id, "gain_db": self.gain_db}
        return {**row, "note": self.note} if self.note else row


@dataclass(frozen=True)
class Plan:
    events: list[Event]
    changes: int  # TV content changes after the opening (and the first one, with tv_on)
    gap_skipped: int  # of those, left without a click (min gap)
    lowered: int  # events lowered under speech
    moved: list[str] = field(default_factory=list)  # human-readable, one per moved event

    @property
    def clicks(self) -> list[Event]:
        return [e for e in self.events if e.event == "tv_click"]

    @property
    def power(self) -> list[Event]:
        return [e for e in self.events if e.event in ("tv_on", "tv_off")]

    @property
    def fx(self) -> list[Event]:
        return [e for e in self.events if e.event == "fx_pop"]

    @property
    def sting(self) -> Event | None:
        return next((e for e in self.events if e.event == "sting"), None)

    def rows(self) -> list[dict]:
        return [e.row() for e in self.events]


class VariantPicker:
    """Seeded per reel and category; never the same sound twice in a row."""

    def __init__(self, sounds: list[Sound], seed: str):
        self.sounds = sounds
        self.seed = seed
        self.rng: dict[str, random.Random] = {}
        self.last: dict[str, str] = {}

    def pick(self, category: str) -> Sound:
        pool = sorted(by_category(self.sounds, category), key=lambda s: s.id)
        if not pool:
            raise SfxError(empty_category_message(category))
        rng = self.rng.setdefault(category, random.Random(f"{self.seed}/sfx/{category}"))
        sound = rng.choice([s for s in pool if s.id != self.last.get(category)] or pool)
        self.last[category] = sound.id
        return sound


def empty_category_message(category: str) -> str:
    return (
        f"No {category} sounds in assets/sfx/; add one with scripts/sfx.py add <file> --category {category} "
        "(see channel docs/sfx.md), or render with --no-sfx"
    )


def first_drawn_frame(t: float, fps: int) -> float:
    """Time of the first frame after `t` (a fade starting at t is still at 0 on t's own frame)."""
    return (math.floor(t * fps) + 1) / fps


def outro_hook(outro_start: float, fps: int) -> float:
    """The outro's first frame, as remotion/src/Reel.tsx computes it (Math.ceil(start * fps))."""
    return math.ceil(outro_start * fps) / fps


def screen_changes(images: list[dict], body_end: float, fps: int) -> list[float]:
    """Times the wall TV's content changes, from screenImages (time order, non-overlapping):
    each image's start, and its end unless the next image starts there (Scene.tsx's
    adjacency test). Changes at or after `body_end` (to idle for the outro, or the
    reel's end) are left out."""
    out = []
    for i, image in enumerate(images):
        out.append(image["start"])
        nxt = images[i + 1] if i + 1 < len(images) else None
        if nxt is None or abs(nxt["start"] - image["end"]) >= 1 / fps:
            out.append(image["end"])
    return sorted(t for t in out if t < body_end - 1e-3)


def speech_spans(words: list[dict]) -> list[tuple[float, float]]:
    return [(w["start"], w["end"]) for w in words if w.get("start") is not None and w.get("end") is not None]


# Spans that only touch (within float noise) don't overlap.
_EPS = 1e-6


def overlaps(start: float, end: float, spans: list[tuple[float, float]]) -> bool:
    return any(a < end - _EPS and b > start + _EPS for a, b in spans)


def check_outro_fit(hold_s: float, fps: int) -> None:
    """Raise SfxError unless the longest allowed sting fits in the end card's hold of
    `hold_s` (outro.json holdS): it waits for the voice under the card (plan()), so it
    starts at the voice's end, a frame and a millisecond of rounding later at most."""
    sting_max = CATEGORIES["sting"]["max_duration_s"]
    late = 1 / fps + 1e-3
    if sting_max + late > hold_s + 1e-9:
        raise SfxError(
            f"A {sting_max:g}s sting starting {late:.3f}s after the voice doesn't fit in the end card's "
            f"{hold_s:g}s hold; lower CATEGORIES sting max_duration_s to at most {hold_s - late:.3f}s in "
            "reelline/media/sfx/config.py (or raise holdS in remotion/src/outro.json)"
        )


def plan(
    sounds: list[Sound],
    seed: str,
    fps: int,
    images: list[dict],
    body_end: float,
    words: list[dict],
    outro_start: float | None,
    tag_span: tuple[float, float] | None,
    reel_end: float,
    gain_offset_db: float = 0.0,
    fx_starts: list[float] = (),
) -> Plan:
    """The reel's SFX events (see the module docstring). `images` are the Remotion
    screenImages, `words` the timed display tokens (speech), `outro_start` None for no
    outro (no "tv_off", no sting), `tag_span` the tag line's (start, end) in the voice track,
    `fx_starts` when each fact graphic's card starts. The end card opens over the voice (at
    `outro_start`, before the tag line, reelline/media/outro.py card_start()): the sting waits for
    the voice under it to end."""
    picker = VariantPicker(sounds, seed)
    speech = speech_spans(words)
    events: list[Event] = []
    changes = gap_skipped = 0
    power: list[Sound] = []  # one tv_power sound per reel, for on and off

    def power_sound() -> Sound:
        if not power:
            power.append(picker.pick("tv_power"))
        return power[0]

    def add(event: str, start: float, sound: Sound, under_speech_db: float) -> Event:
        start = round(max(0.0, start), 3)
        gain = CATEGORIES[sound.category]["gain_db"] + gain_offset_db
        note = None
        if overlaps(start, start + sound.duration_s, speech):
            gain += under_speech_db
            note = f"lowered {under_speech_db:+g} dB: under speech"
        e = Event(event, start, sound, round(gain, 2), note)
        events.append(e)
        return e

    last: float | None = None
    for k, t in enumerate(screen_changes(images, body_end, fps)):
        # The first change is always idle -> image (the TV starts idle).
        if k == 0 and TV_POWER["on"]:
            changes += 1
            if t < TV_CLICK["opening_s"]:
                last = TV_CLICK["opening_s"]
                add("tv_on", last, power_sound(), TV_POWER["under_speech_db"])
            else:
                last = first_drawn_frame(t, fps)
                add("tv_on", last - TV_POWER["pre_offset_s"], power_sound(), TV_POWER["under_speech_db"])
            continue
        if t < TV_CLICK["opening_s"]:
            continue
        changes += 1
        switch = first_drawn_frame(t, fps)
        if last is not None and switch - last < TV_CLICK["min_gap_s"]:
            gap_skipped += 1
            continue
        last = switch
        add("tv_click", switch - TV_CLICK["pre_offset_s"], picker.pick("tv_click"), TV_CLICK["under_speech_db"])

    if FX_POP["on"]:
        for t in fx_starts:
            at = round(t + FX_POP["offset_s"], 3)
            if any(abs(e.time - at) < TV_CLICK["min_gap_s"] for e in events):
                continue
            add("fx_pop", at, picker.pick("tv_click"), FX_POP["under_speech_db"])

    moved = []
    if outro_start is not None:
        hook = outro_hook(outro_start, fps)
        blockers = (
            [] if tag_span is None else [("the voice under the end card", (min(outro_start, tag_span[0]), tag_span[1]))]
        )
        if TV_POWER["off"]:
            # The screen's last image ends at the outro start (body_end); its fade to idle
            # starts on the first frame after it, like any change.
            switch = first_drawn_frame(outro_start, fps)
            off = add("tv_off", switch - TV_POWER["pre_offset_s"], power_sound(), TV_POWER["under_speech_db"])
            blockers.append(
                ("the TV off sound", (off.time, off.time + off.sound.duration_s + STING["gap_after_off_s"]))
            )
        sound = picker.pick("sting")
        t = round(hook + STING["offset_s"], 3)
        notes = []
        # Past everything it would overlap (moving past one can land on another). Times
        # are in ms, rounded up so a move always clears the blocker; each moves it at most once.
        for _ in range(len(blockers) + 1):
            hit = next(((what, span) for what, span in blockers if overlaps(t, t + sound.duration_s, [span])), None)
            if hit is None:
                break
            end = math.ceil(hit[1][1] * 1000 - 1e-6) / 1000
            notes.append(f"moved from {t:.2f}s to the end of {hit[0]} ({end:.2f}s) to not overlap it")
            t = end
        else:
            raise SfxError(f"Couldn't place the sting clear of {', '.join(w for w, _ in blockers)}")
        moved += [f"sting {n}" for n in notes]
        if t + sound.duration_s > reel_end + 1e-3:
            raise SfxError(
                f"Sting {sound.id} ({sound.duration_s:.2f}s) at {t:.2f}s runs past the reel's end ({reel_end:.2f}s); "
                "use a shorter sting, an earlier STING offset_s, or TV_POWER off False in reelline/media/sfx/config.py"
            )
        events.append(
            Event(
                "sting", t, sound, round(CATEGORIES["sting"]["gain_db"] + gain_offset_db, 2), "; ".join(notes) or None
            )
        )
    events.sort(key=lambda e: e.time)
    lowered = sum(1 for e in events if e.note and "under speech" in e.note)
    return Plan(events, changes, gap_skipped, lowered, moved)
