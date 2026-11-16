"""The camera's angle changes (front, 3/4, from behind) and the TV glances they bring."""

from __future__ import annotations

from reelline.media.motion.base import _overlaps, _seeded, config
from reelline.media.motion.markup import cut_points


def _inside(t: float, spans: list[tuple[float, float]]) -> bool:
    return any(a <= t <= b for a, b in spans)


def _no_cut(zooms: list[dict], fx_events: list[dict]) -> list[tuple[float, float]]:
    """Where the camera never cuts: a card in flight (out of the TV's face, flightPadS more, or
    back into it, flightPadS before), a push-in on its way in or held."""
    from reelline.media.fx import back_s
    from reelline.media.fx import config as fx_config

    pad = float(config()["framing"]["flightPadS"])
    out_s = float(fx_config()["placements"]["popout"]["out"]["durationS"])
    no_cut = [(z["start"], z["holdEnd"]) for z in zooms]
    for e in fx_events:
        if not e.get("frame0"):
            no_cut.append((e["start"] + 1e-3, e["start"] + out_s + pad))
        no_cut.append((e["end"] - back_s() - pad, e["end"] - 1e-3))
    return no_cut


def _pick_index(what: str, k: int, n: int) -> int:
    return min(int(_seeded(what, k, 0.0, float(n))), n - 1)


def plan_angles(
    script,
    words: list[dict],
    images: list[dict],
    fx_events: list[dict],
    zooms: list[dict],
    reactions: list[dict],
    body_end: float,
    arrow: tuple[float, float] | None = None,
    available: list[str] | None = None,
) -> list[dict]:
    """[{start, end, angle, shot, why?}] in time order: where the camera leaves the front angle
    (motion.json framing.angles; module docstring). Each starts and ends on a sentence's end
    (cut_points() kind sentence, never during a card's flight or a push-in's way in and hold),
    not before notBeforeS nor within minShotS of the end card, minFrontS of front between two,
    never over what its angle can't show: a fact card outside cardAngles, the share arrow
    (`arrow`) outside arrowAngles and, for an angle without his face (face false), a push-in
    or a reaction pose. kind beat: at most maxPerReel beats of lengthS, seeded picks, each
    where a new TV picture comes up within pictureS of its start (why "picture") while one
    fits, else (`elsewhere`, the reel has pictures) on any other span (why "tv": plan() has
    him glance up at the TV from its start). kind share: seeded spans of lengthS until a
    seeded share of the body in `share`, never over its top. `attempts` seeded placements
    (the beats first, then the shares first, in turn); the one with every share nearest its
    bounds, then the most beats, the most on a picture, wins. Each span's shot is a seeded
    pick of its rule's shots. `available`: the angles the reel's background has art for
    (reelline/media/angles.py available(); None: all)."""
    fc = config()["framing"]
    ac = fc["angles"]
    if not fc["enabled"] or not ac["enabled"]:
        return []
    min_shot, gap = float(fc["minShotS"]), float(ac["minFrontS"])
    no_cut = _no_cut(zooms, fx_events)
    points = [
        p["at"]
        for p in cut_points(script, words, body_end)
        if p["kind"] == "sentence"
        and float(ac["notBeforeS"]) <= p["at"] <= body_end - min_shot
        and not _inside(p["at"], no_cut)
    ]
    cards = [(float(e["start"]), float(e["end"])) for e in fx_events]
    face = [(z["start"], z["end"]) for z in zooms] + [(r["start"], r["end"]) for r in reactions]
    pictures = [float(img["start"]) for img in images[1:]]
    rules = {a: r for a, r in ac["rules"].items() if available is None or a in available}

    def spans(rule: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        """(the spans of lengthS, the ones a beat falls back on): a beat's first are where a new
        picture comes up within pictureS of its start, then (`elsewhere`, a picture on the TV)
        the rest."""
        lo, hi = (float(v) for v in rule["lengthS"])
        out = [(s, e) for i, s in enumerate(points) for e in points[i + 1 :] if lo - 1e-6 <= e - s <= hi + 1e-6]
        if rule["kind"] != "beat":
            return out, []
        p0, p1 = (float(v) for v in rule["pictureS"])
        near = [(s, e) for s, e in out if any(s + p0 - 1e-6 <= t <= s + p1 + 1e-6 for t in pictures)]
        return near, [c for c in out if c not in near] if rule["elsewhere"] and images else []

    options = {angle: spans(rule) for angle, rule in rules.items()}

    def attempt(n: int) -> list[dict]:
        """One seeded placement: the beats first (even n) or the shares first (odd n)."""
        chosen: list[dict] = []

        def fits(angle: str, rule: dict, s: float, e: float) -> bool:
            blocked = (
                (cards if angle not in ac["cardAngles"] else [])
                + (face if not rule["face"] else [])
                + ([arrow] if arrow and angle not in ac["arrowAngles"] else [])
            )
            return not _overlaps((s, e), blocked) and not _overlaps(
                (s - gap, e + gap), [(c["start"], c["end"]) for c in chosen]
            )

        def add(angle: str, rule: dict, span: tuple[float, float], k: int, why: str | None = None) -> None:
            shot = rule["shots"][_pick_index(f"angle-shot-{angle}", k, len(rule["shots"]))]
            chosen.append(
                {"start": span[0], "end": span[1], "angle": angle, "shot": shot, **({"why": why} if why else {})}
            )

        for kind in ("beat", "share") if n % 2 == 0 else ("share", "beat"):
            for angle, rule in rules.items():
                if rule["kind"] != kind:
                    continue
                near, elsewhere = options[angle]
                if kind == "beat":
                    for k in range(int(rule["maxPerReel"])):
                        free = [c for c in near if fits(angle, rule, *c)]
                        why = "picture"
                        if not free:
                            free, why = [c for c in elsewhere if fits(angle, rule, *c)], "tv"
                        if not free:
                            break
                        add(angle, rule, free[_pick_index(f"angle-{angle}:{n}", k, len(free))], k, why)
                    continue
                lo, hi = (float(v) for v in rule["share"])
                target, cap = body_end * _seeded(f"angle-share-{angle}", 0, lo, hi), body_end * hi
                total, k = 0.0, 0
                while total < target - 1e-6:
                    free = [c for c in near if fits(angle, rule, *c) and total + c[1] - c[0] <= cap + 1e-6]
                    if not free:
                        break
                    span = free[_pick_index(f"angle-{angle}:{n}", k, len(free))]
                    add(angle, rule, span, k)
                    total, k = total + span[1] - span[0], k + 1
        return sorted(chosen, key=lambda c: c["start"])

    def score(n: int, chosen: list[dict]) -> tuple:
        # Every share inside its bounds first (the nearer the better), then the most beats, the
        # most of them on a new picture.
        miss = 0.0
        for angle, rule in rules.items():
            if rule["kind"] == "share":
                share = sum(c["end"] - c["start"] for c in chosen if c["angle"] == angle) / body_end
                miss += max(0.0, float(rule["share"][0]) - share, share - float(rule["share"][1]))
        return (
            round(miss, 6),
            -sum(rules[c["angle"]]["kind"] == "beat" for c in chosen),
            -sum(c.get("why") == "picture" for c in chosen),
            n,
        )

    tries = [(n, attempt(n)) for n in range(int(ac["attempts"]))]
    return min(tries, key=lambda t: score(*t))[1]


def beat_glances(
    spans: list[dict], glances: list[dict], reactions: list[dict], stalks: list[dict], left: list[dict]
) -> list[dict]:
    """`glances` plus a glance up at the TV (tvGlance's timings, why "<angle> beat") from the
    start of each beat in another angle where no new picture comes up (plan_angles() why "tv":
    from behind, he turns to the TV); none over a reaction or a stalk reaction, nor within
    tvGlance.minGapS of another glance (left out)."""
    gc = config()["tvGlance"]
    length, gap = float(gc["dartS"]) + float(gc["holdS"]) + float(gc["returnS"]), float(gc["minGapS"])
    out = list(glances)
    for a in spans:
        if a.get("why") != "tv":
            continue
        start = a["start"] + float(gc["delayS"])
        busy = [(x["start"], x["end"]) for x in [*reactions, *stalks]] + [
            (g["start"] - gap, g["end"] + gap) for g in out
        ]
        if _overlaps((start, start + length), busy):
            left.append(
                {
                    "what": "glance",
                    "at": round(start, 3),
                    "reason": f"{a['angle']} beat: a reaction, a stalk reaction or another glance",
                }
            )
            continue
        out.append({"start": round(start, 3), "end": round(start + length, 3), "why": f"{a['angle']} beat"})
    return sorted(out, key=lambda g: g["start"])
