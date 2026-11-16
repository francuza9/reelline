"""The deadline's tag, and when each card is out: planned() and placed()."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from reelline.media.fx.base import DAYS_LEFT_MAX, MONTHS, config

# ---- The deadline's tag -----------------------------------------------------------------


def deadline_date(day: int, month: str, publish: date) -> date:
    """The deadline's date: the day and month in the year that puts it nearest the publish date."""
    m = MONTHS.index(month) + 1
    options = []
    for year in (publish.year - 1, publish.year, publish.year + 1):
        try:
            options.append(date(year, m, day))
        except ValueError:  # 29 February outside a leap year
            continue
    return min(options, key=lambda d: abs((d - publish).days))


def days_left(day: int, month: str, publish: date | None) -> int | None:
    """Days from the publish (slot) date to the deadline; None without a publish date, or
    when it's 0 or less, or more than DAYS_LEFT_MAX."""
    if publish is None:
        return None
    n = (deadline_date(day, month, publish) - publish).days
    return n if 0 < n <= DAYS_LEFT_MAX else None


def publish_date_of(script_path: Path) -> date | None:
    """The slot date in a pipeline script's file name (<YYYY-MM-DD>_<slot>_<slug>.txt), else None."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})_", script_path.name)
    try:
        return date.fromisoformat(m.group(1)) if m else None
    except ValueError:
        return None


# ---- Timing -----------------------------------------------------------------------------


def settle_s(template: str) -> float:
    """From the card's start until its template is still: the card's lead and the template's durationS."""
    spec = config()
    return float(spec["placements"]["popout"]["leadS"]) + float(spec["templates"][template]["durationS"])


def back_s() -> float:
    return float(config()["placements"]["popout"]["back"]["durationS"])


def frame0_on() -> bool:
    """Whether paragraph 1's card is the frame-0 card (fx.json opening.fromFrame0)."""
    return bool(config()["opening"]["fromFrame0"])


def _settled_at(card: dict) -> float:
    """When the card's template is still: frame-0 cards are settled from their start."""
    return card["start"] if card.get("frame0") else card["start"] + settle_s(card["template"])


def _min_hold(card: dict) -> float:
    return float(config()["opening" if card.get("frame0") else "reel"]["minHoldS"])


def planned(script, words: list[dict], shown: set[tuple[int, str]] | None) -> tuple[list[dict], list[dict]]:
    """(cards, left out) before the outro is known: one card per paragraph whose @fx is in
    `shown` ((paragraph, raw) pairs; None: every one), from its anchor word's start (the
    first timed word at or after it in the paragraph) for its full length, back in the TV by
    the next paragraph's first word. When that leaves it under minHoldS, it holds minHoldS
    into the next paragraph if that one has no joke (reelline/media/motion.py joke_paragraphs()) and
    is back before the one after it; else it's left out. Paragraph 1's card, with
    opening.fromFrame0, is the frame-0 card: from 0 to as long as a card on its first word
    would be, back by paragraph 2's first word, but at least opening.minHoldS settled (into
    paragraph 2 when need be, a joke there or not), with frame0 True. With reel.queue (the
    straight style's: a card on every key fact, often on paragraphs in a row) a card that would
    start within gapS of the one before it waits for it instead (placed() would leave it out):
    from gapS after that one's end, if that's at most reel.queueLagS after its paragraph ends,
    for at least minHoldS settled. Each card: {paragraph, template, text, raw, data, start, end[, frame0]}; left out:
    {paragraph, raw, reason}."""
    from reelline.media.motion import joke_paragraphs
    from reelline.media.script_markup import fx_token

    reel = config()["reel"]
    cards, left = [], []
    starts: list[float | None] = [None] * len(script.paragraphs)
    for token, word in zip(script.tokens, words, strict=False):
        if starts[token.paragraph] is None and word["start"] is not None:
            starts[token.paragraph] = word["start"]
    jokes = joke_paragraphs(script)

    def after(n: int) -> float | None:
        """The first paragraph start after 0-based paragraph n's."""
        return next((s for s in starts[n + 1 :] if s is not None), None)

    for n, para in enumerate(script.paragraphs, 1):
        fx = para.fx
        if fx is None:
            continue
        if shown is not None and (n, fx.raw) not in shown:
            left.append({"paragraph": n, "raw": fx.raw, "reason": "not fact-checked as supported"})
            continue
        frame0 = n == 1 and frame0_on()
        # The frame-0 card's length is a card's on the paragraph's first word (no anchor).
        i = script.first_token(n - 1) if frame0 else fx_token(script, n - 1)
        start = next(
            (
                words[k]["start"]
                for k in range(i, len(words))
                if script.tokens[k].paragraph == n - 1 and words[k]["start"] is not None
            ),
            None,
        )
        if start is None:
            left.append({"paragraph": n, "raw": fx.raw, "reason": "no aligned word at or after its anchor"})
            continue
        end = start + settle_s(fx.template) + float(reel["holdS"]) + back_s()
        boundary = after(n - 1)
        if frame0:
            normal = end if boundary is None else min(end, boundary)
            end = max(normal, float(config()["opening"]["minHoldS"]) + back_s())
            cards.append(
                {
                    "paragraph": n,
                    "template": fx.template,
                    "text": fx.text,
                    "raw": fx.raw,
                    "data": dict(fx.data),
                    "start": 0.0,
                    "end": round(end, 3),
                    "frame0": True,
                }
            )
            continue
        if boundary is not None and end > boundary:
            shortest = start + settle_s(fx.template) + float(reel["minHoldS"]) + back_s()
            if boundary >= shortest - 1e-6:
                end = boundary
            else:
                following = next(k for k in range(n, len(starts)) if starts[k] is not None)
                beyond = after(following)
                if following in jokes:
                    left.append(
                        {
                            "paragraph": n,
                            "raw": fx.raw,
                            "reason": f"paragraph {n} is too short for "
                            f"{reel['minHoldS']}s and paragraph {following + 1} has a joke",
                        }
                    )
                    continue
                if beyond is not None and shortest > beyond + 1e-6:
                    left.append(
                        {
                            "paragraph": n,
                            "raw": fx.raw,
                            "reason": f"paragraphs {n} and {following + 1} are too short for {reel['minHoldS']}s",
                        }
                    )
                    continue
                end = shortest
        if reel["queue"] and cards and start < cards[-1]["end"] + float(reel["gapS"]) - 1e-6:
            # It waits for the card before it to be back, gapS more, at most queueLagS past its own
            # paragraph's end (the fact just said); then it holds at least minHoldS settled.
            waited = cards[-1]["end"] + float(reel["gapS"])
            if boundary is not None and waited > boundary + float(reel["queueLagS"]) + 1e-6:
                left.append(
                    {
                        "paragraph": n,
                        "raw": fx.raw,
                        "reason": f"the card before it is out until "
                        f"{cards[-1]['end']:.2f}s, over {reel['queueLagS']}s past paragraph {n}'s end",
                    }
                )
                continue
            end = max(end, waited + settle_s(fx.template) + float(reel["minHoldS"]) + back_s())
            start = waited
        cards.append(
            {
                "paragraph": n,
                "template": fx.template,
                "text": fx.text,
                "raw": fx.raw,
                "data": dict(fx.data),
                "start": round(start, 3),
                "end": round(end, 3),
            }
        )
    return cards, left


def caption_windows(cards: list[dict]) -> list[tuple[float, float]]:
    """The stretches the captions stay on one line: each card's, padded by captionPadS."""
    pad = float(config()["reel"]["captionPadS"])
    return [(c["start"] - pad, c["end"] + pad) for c in cards]


def placed(cards: list[dict], body_end: float, publish: date | None) -> tuple[list[dict], list[dict]]:
    """(events, left out) once the outro's start (`body_end`) is known: each card cut short to
    end gapS before the next one and outroGapS before `body_end`, left out when it would then
    hold less than minHoldS (the frame-0 card: opening.minHoldS; it's never cut for the next
    card, which is left out instead when too close); the deadline's daysLeft from `publish`.
    Events are Remotion's FxEvent ({data, placement, start, end, paragraph[, frame0]})."""
    reel = config()["reel"]
    events: list[dict] = []
    left: list[dict] = []
    for k, c in enumerate(cards):
        end = min(c["end"], body_end - float(reel["outroGapS"]))
        if k + 1 < len(cards) and not c.get("frame0"):
            end = min(end, cards[k + 1]["start"] - float(reel["gapS"]))
        if events and c["start"] < events[-1]["end"] + float(reel["gapS"]) - 1e-6:
            left.append({"paragraph": c["paragraph"], "raw": c["raw"], "reason": "too close to the card before it"})
            continue
        hold = end - back_s() - _settled_at(c)
        if hold < _min_hold(c) - 1e-6:
            left.append(
                {
                    "paragraph": c["paragraph"],
                    "raw": c["raw"],
                    "reason": f"it would hold {max(hold, 0):.2f}s, under {_min_hold(c)}s, before "
                    + (
                        "the end card"
                        if end < c["end"] and (k + 1 == len(cards) or c.get("frame0"))
                        else "the next card"
                    ),
                }
            )
            continue
        data = dict(c["data"])
        if data["template"] == "deadline":
            data["daysLeft"] = days_left(data["day"], data["month"], publish)
        events.append(
            {
                "data": data,
                "placement": reel["placement"],
                "start": c["start"],
                "end": round(end, 3),
                "paragraph": c["paragraph"],
                **({"frame0": True} if c.get("frame0") else {}),
            }
        )
    return events, left
