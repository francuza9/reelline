"""The camera's framings (wide, medium, close) between the angle changes."""

from __future__ import annotations

from collections.abc import Sequence

from reelline.media.motion.angles import _inside, _no_cut
from reelline.media.motion.base import INF, _overlaps, _seeded, config
from reelline.media.motion.markup import cut_points


def plan_shots(
    script,
    words: list[dict],
    images: list[dict],
    fx_events: list[dict],
    zooms: list[dict],
    body_end: float,
    arrow: tuple[float, float] | None = None,
    angles: Sequence[dict] = (),
) -> list[dict]:
    """[{start, shot, angle?}] from 0 (framing.first), each shot until the next (module docstring;
    motion.json framing): cut on a cut_points() boundary nearest a seeded length in lengthS (a
    sentence end preferred), within minShotS..maxShotS, never during a card's flight or a
    push-in's way in and hold; the next shot a seeded pick by `weights` among the others it may be. A close shot
    (or one not in cardShots) is only cut to when it can be cut out of before what it can't
    show (a card; a new picture's first tvReadS; the share arrow, `arrow`, outside arrowShots:
    it's drawn for the wide frame and would cross the mascot's face in a closer one) on a boundary
    minShotS on. A push-in inside a close shot is plan()'s to drop (the close-up is the push-in).
    `angles` (plan_angles()): each span is one shot in its angle ({start, shot, angle}); the
    front shots before it leave minShotS clear of it (its cut may end the last one), and the
    first after it is a seeded pick by `weights` (any framing, the span's own too: the angle
    changes) from the span's end. Front shots carry no angle."""
    fc = config()["framing"]
    shots_cfg, weights = fc["shots"], fc["weights"]
    min_shot, max_shot = float(fc["minShotS"]), float(fc["maxShotS"])
    no_cut = _no_cut(zooms, fx_events)
    cards = [(float(e["start"]), float(e["end"])) for e in fx_events]
    pictures = [
        (float(img["start"]), float(img["start"]) + float(fc["tvReadS"]))
        for img in images[1:]
        if float(img["start"]) > 0 and float(fc["tvReadS"]) > 0
    ]
    points = [
        c
        for c in cut_points(script, words, body_end)
        if float(fc["notBeforeS"]) <= c["at"] <= body_end - min_shot and not _inside(c["at"], no_cut)
    ]

    def blockers(name: str) -> list[tuple[float, float]]:
        spec = shots_cfg[name]
        return sorted(
            (cards if name not in fc["cardShots"] or spec["close"] else [])
            + (pictures if spec["close"] else [])
            + ([arrow] if arrow and name not in fc["arrowShots"] else [])
        )

    def hard_end(name: str, at: float) -> float | None:
        """Where a shot from `at` must be cut out of by (inf: nothing ahead); None: it can't start."""
        spans = blockers(name)
        if _overlaps((at, at + 1e-3), spans):
            return None
        return min((a for a, _ in spans if a > at), default=INF)

    def may_start(name: str, at: float, stop: float) -> bool:
        end = hard_end(name, at)
        return end is not None and (end >= min(body_end, stop) or any(at + min_shot <= p["at"] <= end for p in points))

    def pick(k: int, current: str | None, at: float, stop: float) -> str | None:
        names = [n for n in shots_cfg if n != current]
        r = _seeded("shot-pick", k, 0.0, sum(float(weights[n]) for n in names))
        order: list[str] = []
        for n in names:
            r -= float(weights[n])
            if r < 0 and not order:
                order.append(n)
        order += [n for n in names if n not in order]
        return next((n for n in order if may_start(n, at, stop)), None)

    spans = sorted(angles, key=lambda a: a["start"])
    shots = [{"start": 0.0, "shot": fc["first"]}]
    for i in range(len(spans) + 1):
        # The front stretch up to the next angle's cut (`stop`), which may end its last shot.
        stop = spans[i]["start"] if i < len(spans) else INF
        while True:
            k, cur = len(shots) - 1, shots[-1]
            target = cur["start"] + _seeded("shot-length", k, *fc["lengthS"])
            blocker = hard_end(cur["shot"], cur["start"]) or INF
            end = min(blocker, stop)
            lo, hi = cur["start"] + min_shot, min(cur["start"] + max_shot, end, stop - min_shot)
            options = [p for p in points if lo - 1e-6 <= p["at"] <= hi + 1e-6]
            if stop <= min(cur["start"] + max_shot, blocker) + 1e-6:
                options.append({"at": stop, "kind": "sentence", "angle": True})
            if not options:
                # Nothing in reach: a close shot leaves on its last boundary in time, others on the next one.
                options = (
                    [p for p in points if cur["start"] < p["at"] <= blocker][-1:]
                    if blocker < stop
                    else [p for p in points if hi < p["at"] <= stop - min_shot][:1]
                )
            ranked = sorted(
                options,
                key=lambda p: (
                    abs(p["at"] - target) - (float(fc["sentenceBonusS"]) if p["kind"] == "sentence" else 0.0),
                    p["at"],
                ),
            )
            nxt = None
            for p in ranked:
                name = None if p.get("angle") else pick(k, cur["shot"], p["at"], stop)
                if p.get("angle") or name:
                    nxt = (p, name)
                    break
            if nxt is None or nxt[1] is None:
                break
            shots.append({"start": nxt[0]["at"], "shot": nxt[1]})
        if i == len(spans):
            return shots
        span = spans[i]
        shots.append({"start": span["start"], "shot": span["shot"], "angle": span["angle"]})
        after = spans[i + 1]["start"] if i + 1 < len(spans) else INF
        shots.append({"start": span["end"], "shot": pick(len(shots) - 1, None, span["end"], after) or fc["first"]})
    return shots
