"""The motion plan: push-ins, reactions, glances, bounces, stalks and lean-ins."""

from __future__ import annotations

from reelline.media.motion.angles import beat_glances, plan_angles
from reelline.media.motion.base import INF, _overlaps, _seeded, _why, config
from reelline.media.motion.beats import _close_spans, _rig_beats, beats, fill_beats
from reelline.media.motion.framing import check_framing
from reelline.media.motion.markup import bounce_moments, emphasis_words, punchlines, stalk_pose
from reelline.media.motion.shots import plan_shots

# ---- The plan -----------------------------------------------------------------------------------


def plan(
    script,
    words: list[dict],
    images: list[dict],
    fx_events: list[dict],
    body_end: float,
    fx_lead_s: float = 0.0,
    mood: str | None = None,
    anchors: dict | None = None,
    screen: dict | None = None,
    arrow: tuple[float, float] | None = None,
    angles: list[str] | None = None,
) -> dict:
    """Remotion's `motion` prop plus what was left out and the beats: {zooms: [{start, peak,
    holdEnd, end, scale, paragraph}], reactions: [{start, end, pose, paragraph}], glances:
    [{start, end, why}], shots: [{start, shot, angle?}], leans: [{start, peak, holdEnd, end,
    paragraph, why}], stalks: [{start, end, pose, paragraph}], bounces: [{at, paragraph, why}],
    angles: [{start, end, angle, shot}], body_end, left: [{what, paragraph?, at, reason}],
    beats: [{at, what}]}. `images` are the TV's pictures (screenImages), `fx_events` the fact graphics
    (reelline/media/fx.py placed()), `body_end` the end card's start, `mood` the reel's
    (stalks.byMood), `anchors` and `screen` the render's (the framing's check; default the
    repo's), `arrow` the share arrow's (start, end) (reelline/media/share.py share_props(); None: no
    arrow), `angles` the camera angles the reel's background has art for (reelline/media/angles.py
    available(); None: every one)."""
    cfg = config()
    pc, zc, rc, gc = cfg["punchline"], cfg["zoom"], cfg["reaction"], cfg["tvGlance"]
    fx_spans = [(e["start"], e["end"]) for e in fx_events]
    card = [(body_end, INF)]
    left: list[dict] = []
    if cfg["framing"]["enabled"]:
        check_framing(anchors, screen)

    # Push-ins: the closing jab first, then in time order; minGapS apart.
    candidates = punchlines(script, words)
    chosen: list[dict] = []
    for p in sorted(candidates, key=lambda p: (p["source"] != "jab", p["land"])):
        if p["land"] < float(pc["notBeforeS"]):
            left.append(
                {
                    "what": "punchline",
                    "paragraph": p["paragraph"],
                    "at": p["land"],
                    "reason": f"before {pc['notBeforeS']}s (the opening)",
                }
            )
        elif len(chosen) >= int(pc["maxPerReel"]):
            left.append(
                {
                    "what": "push-in",
                    "paragraph": p["paragraph"],
                    "at": p["land"],
                    "reason": f"over the {pc['maxPerReel']} per reel",
                }
            )
        elif any(abs(p["land"] - c["land"]) < float(pc["minGapS"]) for c in chosen):
            left.append(
                {
                    "what": "push-in",
                    "paragraph": p["paragraph"],
                    "at": p["land"],
                    "reason": f"within {pc['minGapS']}s of another",
                }
            )
        else:
            chosen.append(p)
    zooms = []
    for k, p in enumerate(sorted(chosen, key=lambda p: p["land"])):
        land = p["land"]
        hold_end = land + float(zc["holdS"])
        span = (land - float(zc["inS"]), hold_end + float(zc["outS"]))
        # The end card comes in over the last line (reelline/media/outro.py card_start()): a push-in seen
        # up to the end of its hold before it may ease back out under it (the card covers that).
        hit = _overlaps(span, fx_spans) or _overlaps((span[0], hold_end), card)
        if hit:
            left.append(
                {"what": "zoom", "paragraph": p["paragraph"], "at": land, "reason": f"it would meet {_why(hit)}"}
            )
        else:
            zooms.append(
                {
                    "start": round(span[0], 3),
                    "peak": land,
                    "holdEnd": round(hold_end, 3),
                    "end": round(span[1], 3),
                    "scale": round(_seeded("zoom", k, *zc["scale"]), 4),
                    "paragraph": p["paragraph"],
                }
            )

    # Reaction poses: after every joke whose tag has one; the closing jab first, then in time order.
    reactions: list[dict] = []
    later = [p for p in candidates if p["land"] >= float(pc["notBeforeS"])]
    gap = float(rc["minGapS"])
    for k, p in sorted(enumerate(later), key=lambda kp: (kp[1]["source"] != "jab", kp[1]["land"])):
        start = p["land"] + float(rc["delayS"])
        span = (start, start + _seeded("reaction", k, *rc["lengthS"]))
        # Likewise a reaction seen at least half way before the card.
        hit = _overlaps(span, fx_spans) or _overlaps((start, (span[0] + span[1]) / 2), card)
        if hit:
            left.append(
                {
                    "what": "reaction",
                    "paragraph": p["paragraph"],
                    "at": p["land"],
                    "reason": f"it would meet {_why(hit)}",
                }
            )
        elif len(reactions) >= int(rc["maxPerReel"]):
            left.append(
                {
                    "what": "reaction",
                    "paragraph": p["paragraph"],
                    "at": p["land"],
                    "reason": f"over the {rc['maxPerReel']} per reel",
                }
            )
        elif _overlaps((span[0] - gap, span[1] + gap), [(r["start"], r["end"]) for r in reactions]):
            left.append(
                {
                    "what": "reaction",
                    "paragraph": p["paragraph"],
                    "at": p["land"],
                    "reason": f"within {rc['minGapS']}s of another",
                }
            )
        else:
            reactions.append(
                {
                    "start": round(span[0], 3),
                    "end": round(span[1], 3),
                    "pose": rc["byTag"][p["source"]],
                    "paragraph": p["paragraph"],
                }
            )
    reactions.sort(key=lambda r: r["start"])

    # TV glances: each picture change after the first, each fact graphic once it's out (the
    # frame-0 card is out from the first frame: no pop-out to look at).
    moments = [(float(img["start"]), "picture") for img in images[1:]]
    moments += [(float(e["start"]) + fx_lead_s, "fact graphic") for e in fx_events if not e.get("frame0")]
    glances: list[dict] = []
    length = float(gc["dartS"]) + float(gc["holdS"]) + float(gc["returnS"])
    reaction_spans = [(r["start"], r["end"]) for r in reactions]
    for at, what in sorted(moments):
        start = at + float(gc["delayS"])
        span = (start, start + length)
        hit = _overlaps(span, [*reaction_spans, (body_end, INF)])
        reason = (
            "before the opening's end"
            if start < float(gc["notBeforeS"])
            else ("the end card" if hit[1] == INF else "a reaction")
            if hit
            else "right after another"
            if glances and start < glances[-1]["end"] + float(gc["minGapS"])
            else None
        )
        if reason:
            left.append({"what": "glance", "at": round(at, 3), "reason": f"{what}: {reason}"})
        else:
            glances.append({"start": round(start, 3), "end": round(span[1], 3), "why": what})

    bounces, stalks, leans = [], [], []
    if cfg["bounce"]["enabled"]:
        bounces = plan_bounces(script, words, body_end, left)
    if cfg["stalks"]["enabled"]:
        stalks = plan_stalks(script, words, glances, reactions, body_end, mood, left)
    if cfg["lean"]["enabled"]:
        leans = plan_leans(script, words, zooms, reactions, stalks, body_end, left)
    shots: list[dict] = []
    spans: list[dict] = []
    if cfg["framing"]["enabled"]:
        spans = plan_angles(script, words, images, fx_events, zooms, reactions, body_end, arrow, angles)
        glances = beat_glances(spans, glances, reactions, stalks, left)
        shots = plan_shots(script, words, images, fx_events, zooms, body_end, arrow, spans)
        # A close shot is the push-in: none inside one (its geometry has no room for it).
        close = _close_spans(shots)
        for z in [z for z in zooms if _overlaps((z["start"], z["end"]), close)]:
            zooms.remove(z)
            left.append({"what": "zoom", "paragraph": z["paragraph"], "at": z["peak"], "reason": "in a close shot"})

    # The beats: fillers where the reel goes too long without a change.
    others = [*_rig_beats(leans, stalks, bounces)]
    extra_zooms, extra_glances, beat_left = fill_beats(
        script, words, images, fx_events, zooms, reactions, glances, body_end, shots=shots, others=others
    )
    zooms = sorted([*zooms, *extra_zooms], key=lambda z: z["start"])
    glances = sorted([*glances, *extra_glances], key=lambda g: g["start"])
    left += beat_left
    found = [
        b
        for b in beats(images, fx_events, zooms, reactions, glances, shots, leans, stalks, bounces)
        if b["at"] <= body_end
    ]
    return {
        "zooms": zooms,
        "reactions": reactions,
        "glances": glances,
        "shots": shots,
        "leans": leans,
        "stalks": stalks,
        "bounces": bounces,
        "angles": spans,
        "body_end": round(body_end, 3),
        "left": left,
        "beats": found,
    }


def plan_bounces(script, words: list[dict], body_end: float, left: list[dict]) -> list[dict]:
    """[{at, paragraph, why}]: bounce_moments() at least bounce.minGapS apart, none before
    notBeforeS or running into the end card (`left` gets the rest)."""
    bc = config()["bounce"]
    out: list[dict] = []
    for m in bounce_moments(script, words):
        reason = (
            f"before {bc['notBeforeS']}s"
            if m["at"] < float(bc["notBeforeS"])
            else "the end card"
            if m["at"] + float(bc["durationS"]) > body_end
            else f"within {bc['minGapS']}s of the one before"
            if out and m["at"] < out[-1]["at"] + float(bc["minGapS"])
            else None
        )
        if reason:
            left.append(
                {"what": "bounce", "paragraph": m["paragraph"], "at": m["at"], "reason": f"{m['why']}: {reason}"}
            )
        else:
            out.append(m)
    return out


def plan_stalks(
    script,
    words: list[dict],
    glances: list[dict],
    reactions: list[dict],
    body_end: float,
    mood: str | None,
    left: list[dict],
) -> list[dict]:
    """[{start, end, pose, paragraph}]: each paragraph's stalk_pose() (not paragraph 1's: the
    opener) on its first word, moved past a glance or a reaction (at most stalks.maxShiftS),
    minGapS apart; `left` gets the rest."""
    sc = config()["stalks"]
    busy = sorted([(g["start"], g["end"]) for g in glances] + [(r["start"], r["end"]) for r in reactions])
    out: list[dict] = []
    for n, para in enumerate(script.paragraphs):
        pose = stalk_pose(para, mood)
        first = next(
            (w["start"] for i, w in enumerate(words) if script.tokens[i].paragraph == n and w["start"] is not None),
            None,
        )
        if n == 0 or para.tag or pose is None or first is None:
            continue
        at = first + float(sc["delayS"])
        length = _seeded("stalks", n, *sc["lengthS"])
        start = at
        while (hit := _overlaps((start, start + length), busy)) and hit[1] + 0.05 - at <= float(sc["maxShiftS"]):
            start = hit[1] + 0.05
        span = (start, start + length)
        reason = (
            "a glance or a reaction"
            if _overlaps(span, busy)
            else "the end card"
            if span[1] > body_end
            else f"within {sc['minGapS']}s of the one before"
            if out and span[0] < out[-1]["end"] + float(sc["minGapS"])
            else None
        )
        if reason:
            left.append({"what": "stalks", "paragraph": n + 1, "at": round(at, 3), "reason": f"{pose}: {reason}"})
        else:
            out.append({"start": round(span[0], 3), "end": round(span[1], 3), "pose": pose, "paragraph": n + 1})
    return out


def plan_leans(
    script,
    words: list[dict],
    zooms: list[dict],
    reactions: list[dict],
    stalks: list[dict],
    body_end: float,
    left: list[dict],
) -> list[dict]:
    """[{start, peak, holdEnd, end, paragraph, why}]: a lean-in on emphasis_words() (the reveals
    first, then in time order; lean.maxPerReel, minGapS apart), landing on the word's start and
    held over it, never over a push-in, a reaction pose or a stalk reaction, nor the end card."""
    lc = config()["lean"]
    lo, hi = (float(v) for v in lc["holdS"])
    busy = (
        [(z["start"], z["end"]) for z in zooms]
        + [(r["start"], r["end"]) for r in reactions]
        + [(s["start"], s["end"]) for s in stalks]
        + [(body_end, INF)]
    )
    out: list[dict] = []
    for e in sorted(emphasis_words(script, words), key=lambda e: (e["why"] != "reveal", e["at"])):
        hold_end = min(max(e["end"], e["at"] + lo), e["at"] + hi)
        span = (e["at"] - float(lc["inS"]), hold_end + float(lc["outS"]))
        gap = float(lc["minGapS"])
        reason = (
            f"before {lc['notBeforeS']}s"
            if span[0] < float(lc["notBeforeS"])
            else f"over the {lc['maxPerReel']} per reel"
            if len(out) >= int(lc["maxPerReel"])
            else "the end card"
            if span[1] > body_end
            else "another move"
            if _overlaps(span, busy)
            else f"within {gap}s of another"
            if _overlaps((span[0] - gap, span[1] + gap), [(o["start"], o["end"]) for o in out])
            else None
        )
        if reason:
            left.append({"what": "lean", "paragraph": e["paragraph"], "at": e["at"], "reason": f"{e['why']}: {reason}"})
        else:
            out.append(
                {
                    "start": round(span[0], 3),
                    "peak": e["at"],
                    "holdEnd": round(hold_end, 3),
                    "end": round(span[1], 3),
                    "paragraph": e["paragraph"],
                    "why": e["why"],
                }
            )
    return sorted(out, key=lambda o: o["start"])
