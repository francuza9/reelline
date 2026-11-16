"""The beats (visual changes), the fill for long gaps, and the render log summary."""

from __future__ import annotations

import math
from collections.abc import Sequence

from reelline.media.motion.base import INF, _overlaps, _seeded, config
from reelline.media.motion.markup import _SENTENCE_END

# ---- The beats ----------------------------------------------------------------------------------


def _rig_beats(leans: Sequence[dict], stalks: Sequence[dict], bounces: Sequence[dict]) -> list[dict]:
    return [
        *({"at": x["start"], "what": "lean-in"} for x in leans),
        *({"at": x["start"], "what": f"stalks {x['pose']}"} for x in stalks),
        *({"at": x["at"], "what": "bounce"} for x in bounces),
    ]


def beats(
    images: list[dict],
    fx_events: list[dict],
    zooms: list[dict],
    reactions: list[dict],
    glances: list[dict],
    shots: Sequence[dict] = (),
    leans: Sequence[dict] = (),
    stalks: Sequence[dict] = (),
    bounces: Sequence[dict] = (),
) -> list[dict]:
    """The reel's visual changes, [{at, what}] in time order: frame 0 (the opening), each card
    springing out (not the frame-0 card: it's out from frame 0) and flying back, each TV
    picture change after frame 0 while no card covers the TV, each cut after frame 0, and the
    start of each zoom, reaction, planned glance, lean-in, stalk reaction and bounce. Reads
    Remotion's props as they are (QA reuses it)."""
    from reelline.media.fx import back_s

    cards = [(float(e["start"]), float(e["end"])) for e in fx_events]
    out = [{"at": 0.0, "what": "opening"}]
    for e in fx_events:
        if not e.get("frame0"):
            out.append({"at": float(e["start"]), "what": f"card ¶{e.get('paragraph', '?')} out"})
        out.append({"at": round(float(e["end"]) - back_s(), 3), "what": f"card ¶{e.get('paragraph', '?')} back"})
    for img in images:
        t = float(img["start"])
        if t > 0 and not any(a <= t < b for a, b in cards):
            out.append({"at": t, "what": "picture"})
    out += [
        {"at": s["start"], "what": f"cut to {s['shot']}" + (f" ({s['angle']})" if s.get("angle") else "")}
        for s in shots
        if s["start"] > 0
    ]
    out += [{"at": z["start"], "what": "push-in" + (" (beat)" if z.get("why") == "beat" else "")} for z in zooms]
    out += [{"at": r["start"], "what": f"reaction {r['pose']}"} for r in reactions]
    out += [{"at": g["start"], "what": f"glance ({g.get('why', 'tv')})"} for g in glances]
    out += _rig_beats(leans, stalks, bounces)
    return sorted(out, key=lambda b: b["at"])


def largest_gap(beat_list: list[dict], end_s: float, start_s: float = 0.0) -> tuple[float, float]:
    """(gap, where it starts): the longest stretch without a beat in [start_s, end_s], both
    ends counting as one."""
    times = sorted({b["at"] for b in beat_list if start_s <= b["at"] <= end_s} | {start_s, end_s})
    return max(((b - a, a) for a, b in zip(times, times[1:], strict=False)), default=(0.0, 0.0))


def _close_spans(shots: list[dict]) -> list[tuple[float, float]]:
    shots_cfg = config()["framing"]["shots"]
    ends = [s["start"] for s in shots[1:]] + [INF]
    return [(s["start"], end) for s, end in zip(shots, ends, strict=False) if shots_cfg.get(s["shot"], {}).get("close")]


def _faceless_spans(shots: list[dict]) -> list[tuple[float, float]]:
    """The shots in an angle without the mascot's face (framing.angles.rules face false: from behind)."""
    rules = config()["framing"]["angles"]["rules"]
    ends = [s["start"] for s in shots[1:]] + [INF]
    return [
        (s["start"], end)
        for s, end in zip(shots, ends, strict=False)
        if s.get("angle") in rules and not rules[s["angle"]]["face"]
    ]


def fill_beats(
    script,
    words: list[dict],
    images: list[dict],
    fx_events: list[dict],
    zooms: list[dict],
    reactions: list[dict],
    glances: list[dict],
    body_end: float,
    shots: Sequence[dict] = (),
    others: Sequence[dict] = (),
) -> tuple[list[dict], list[dict], list[dict]]:
    """(zooms, glances, left out) to add so the first beats.windowS seconds have a beat at
    least every beats.maxGapS, and the rest of the reel every beats.body.maxGapS (module
    docstring; motion.json beats). Fillers are spread evenly over each long stretch, each
    snapped to a word's end; zooms {start, peak, holdEnd, end, scale, paragraph, why: "beat"},
    glances {start, end, why: "beat"}. `shots` are the camera's (no filler zoom in a close
    shot or over a cut), `others` more beats ({at}: lean-ins, stalk reactions, bounces)."""
    cfg = config()
    bc, zc, gc = cfg["beats"], cfg["zoom"], cfg["tvGlance"]
    if not bc["enabled"]:
        return [], [], []
    window, spacing = float(bc["windowS"]), float(bc["minSpacingS"])
    snap, not_before, bonus = float(bc["snapS"]), float(bc["notBeforeS"]), float(bc["sentenceEndBonusS"])
    zoom_in, zoom_len = float(zc["inS"]), float(zc["inS"]) + float(zc["holdS"]) + float(zc["outS"])
    glance_len = float(gc["dartS"]) + float(gc["holdS"]) + float(gc["returnS"])
    cards = [(float(e["start"]), float(e["end"])) for e in fx_events]
    cuts = [(s["start"] - 1e-3, s["start"] + 1e-3) for s in shots[1:]]
    no_zoom = [*cards, *_close_spans(list(shots)), *_faceless_spans(list(shots)), *cuts]
    new_zooms: list[dict] = []
    new_glances: list[dict] = []
    left: list[dict] = []
    turn = 0

    def candidate(kind: str, end: float, paragraph: int, others_at: list[float], lo: float, hi: float):
        start = end - zoom_in if kind == "zoom" else end
        span = (start, start + (zoom_len if kind == "zoom" else glance_len))
        moving = (
            [(z["start"], z["end"]) for z in [*zooms, *new_zooms]]
            + [(r["start"], r["end"]) for r in reactions]
            + [(g["start"], g["end"]) for g in [*glances, *new_glances]]
        )
        blocked = [*moving, (body_end, INF)] + (no_zoom if kind == "zoom" else [])
        if (
            not lo <= start <= hi
            or start < not_before
            or _overlaps(span, blocked)
            or any(abs(start - b) < spacing - 1e-6 for b in others_at)
        ):
            return None
        return {
            "kind": kind,
            "start": round(start, 3),
            "end": round(span[1], 3),
            "land": round(end, 3),
            "paragraph": paragraph,
        }

    def fill(horizon: float, max_gap: float) -> None:
        nonlocal turn
        # Where a filler may land: a word's end (not inside a group, not in the tag), sentence ends marked.
        ends = [
            (float(w["end"]), bool(_SENTENCE_END.search(w["text"])), script.tokens[i].paragraph + 1)
            for i, w in enumerate(words)
            if w["end"] is not None
            and not script.tokens[i].glue
            and not script.paragraphs[script.tokens[i].paragraph].tag
            and w["end"] < min(body_end, horizon)
        ]
        t = 0.0
        while t < horizon:
            current = beats(images, fx_events, [*zooms, *new_zooms], reactions, [*glances, *new_glances], shots) + list(
                others
            )
            times = sorted({b["at"] for b in current})
            nxt = next((b for b in times if b > t + 1e-6), INF)
            stop = min(nxt, horizon)
            if stop - t <= max_gap + 1e-6:
                if nxt >= horizon:
                    break
                t = nxt
                continue
            count = math.ceil((stop - t) / max_gap - 1e-9)  # stretches the gap is cut into
            ideal = t + (stop - t) / count
            # Between the beats around the gap (minSpacingS clear of both; the horizon isn't one).
            lo, hi = t + spacing, stop - (spacing if nxt <= horizon else 0.0)
            best = None
            for i in range(len(bc["kinds"])):
                kind = bc["kinds"][(turn + i) % len(bc["kinds"])]
                found = []
                for end, sentence, paragraph in ends:
                    c = candidate(kind, end, paragraph, times, lo, hi)
                    if c is not None and abs(c["start"] - ideal) <= snap + 1e-6:
                        found.append((abs(c["start"] - ideal) - (bonus if sentence else 0.0), c["start"], c))
                if found:
                    best = min(found, key=lambda f: f[:2])[2]
                    break
            if best is None:
                left.append(
                    {
                        "what": "beat",
                        "at": round(ideal, 3),
                        "reason": f"no push-in or glance fits near {ideal:.2f}s ({t:.2f}-{stop:.2f}s without a change)",
                    }
                )
                if nxt >= horizon:
                    break
                t = nxt
                continue
            k = len(new_zooms) + len(new_glances)
            if best["kind"] == "zoom":
                land = best["land"]
                hold_end = land + float(zc["holdS"])
                new_zooms.append(
                    {
                        "start": best["start"],
                        "peak": land,
                        "holdEnd": round(hold_end, 3),
                        "end": best["end"],
                        "scale": round(_seeded("beat-zoom", k, *bc["zoomScale"]), 4),
                        "paragraph": best["paragraph"],
                        "why": "beat",
                    }
                )
            else:
                new_glances.append({"start": best["start"], "end": best["end"], "why": "beat"})
            turn += 1
            t = best["start"]

    fill(window, float(bc["maxGapS"]))
    if bc["body"]["enabled"]:
        fill(body_end, float(bc["body"]["maxGapS"]))
    return new_zooms, new_glances, left


def angle_shares(spans: list[dict], body_end: float) -> dict[str, float]:
    """Each angle's share of the body (frame 0 to the end card), front's included."""
    out: dict[str, float] = {}
    for s in spans:
        out[s["angle"]] = out.get(s["angle"], 0.0) + (s["end"] - s["start"]) / body_end
    return {"front": 1.0 - sum(out.values()), **out}


def angle_summary(spans: list[dict], body_end: float | None) -> str:
    if not spans:
        return "front only"
    per: dict[str, list[dict]] = {}
    for s in spans:
        per.setdefault(s["angle"], []).append(s)
    parts = [
        f"{a} "
        + ", ".join(f"{s['start']:.2f}-{s['end']:.2f}" for s in rows)
        + f" ({sum(s['end'] - s['start'] for s in rows):.1f}s"
        for a, rows in per.items()
    ]
    if body_end:
        shares = angle_shares(spans, body_end)
        parts = [p + f", {shares[a]:.0%})" for p, a in zip(parts, per, strict=False)] + [f"front {shares['front']:.0%}"]
    else:
        parts = [p + ")" for p in parts]
    return "; ".join(parts)


def summary(motion: dict) -> list[str]:
    """The render log's lines."""
    zooms = (
        ", ".join(
            f"¶{z['paragraph']} {z['peak']:.2f}s x{z['scale']:.2f}" + (" (beat)" if z.get("why") == "beat" else "")
            for z in motion["zooms"]
        )
        or "none"
    )
    reactions = (
        ", ".join(f"¶{r['paragraph']} {r['pose']} {r['start']:.2f}-{r['end']:.2f}s" for r in motion["reactions"])
        or "none"
    )
    glances = ", ".join(f"{g['start']:.2f}s ({g['why']})" for g in motion["glances"]) or "none"
    lines = [f"Motion: zooms {zooms}", f"        reactions {reactions}", f"        TV glances {glances}"]
    if motion.get("shots"):
        lines.append(
            "        shots "
            + ", ".join(
                f"{s['start']:.2f} {s['shot']}" + (f" ({s['angle']})" if s.get("angle") else "")
                for s in motion["shots"]
            )
        )
    if motion.get("angles") is not None and motion.get("shots"):
        lines.append("        angles " + angle_summary(motion["angles"], motion.get("body_end")))
    if motion.get("leans") or motion.get("stalks") or motion.get("bounces"):
        lines.append(
            "        lean-ins "
            + (
                ", ".join(f"¶{x['paragraph']} {x['peak']:.2f}s ({x['why']})" for x in motion.get("leans") or [])
                or "none"
            )
            + "; stalks "
            + (
                ", ".join(f"¶{x['paragraph']} {x['pose']} {x['start']:.2f}s" for x in motion.get("stalks") or [])
                or "none"
            )
            + "; bounces "
            + (", ".join(f"{x['at']:.2f}s ({x['why']})" for x in motion.get("bounces") or []) or "none")
        )
    if motion.get("beats"):
        bc = config()["beats"]
        window = float(bc["windowS"])
        gap, at = largest_gap(motion["beats"], window)
        lines.append(
            f"        beats 0-{window:g}s: "
            + ", ".join(f"{b['at']:.2f} {b['what']}" for b in motion["beats"] if b["at"] <= window)
            + f" (longest gap {gap:.2f}s from {at:.2f}s)"
        )
        later = [b for b in motion["beats"] if b["at"] > window]
        if later:
            end = max(b["at"] for b in later)
            gap, at = largest_gap(motion["beats"], end, window)
            lines.append(
                f"        beats {window:g}s-: "
                + ", ".join(f"{b['at']:.2f} {b['what']}" for b in later)
                + f" (longest gap {gap:.2f}s from {at:.2f}s; max {bc['body']['maxGapS']:g}s)"
            )
    lines += [
        f"        left out: {x['what']}"
        + (f" ¶{x['paragraph']}" if "paragraph" in x else "")
        + f" at {x['at']:.2f}s ({x['reason']})"
        for x in motion["left"]
    ]
    return lines
