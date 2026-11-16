"""The review's status block and the reel report."""

from __future__ import annotations

import json
import sqlite3

from reelline.config import conf
from reelline.notify.text import about_reel
from reelline.paths import stored_path
from reelline.pipeline.produce.base import ProduceError, short_error


def cost_line(conn: sqlite3.Connection, reel_id: int) -> str:
    """ "Cost so far": the reel's llm_calls as the cost report counts them
    (reelline/pipeline/costs.py reel_costs(): its own render spend, its script's and brief's
    while it's their last reel), so it matches the reel's total there."""
    from reelline.pipeline.costs import reel_costs, reel_parts

    cost = reel_parts(reel_costs(conn, reel_id))
    bound = " (upper bound)"
    parts = " + ".join(
        f"{label} ${x:.4f}{bound if label == 'images' and cost['upper_bound'] else ''}" for label, x in cost["parts"]
    )
    text = f"Cost so far: ${cost['total']:.4f} ({parts})"
    if cost["unpriced"]:
        text += f"; no price for {', '.join(cost['unpriced'])}"
    return text


def qa_lines(reel: sqlite3.Row) -> list[str]:
    """The status block's QA part; [] for a reel QA never looked at (before migration 9)."""
    status = reel["qa_status"]
    if status is None:
        return []
    if status == "skipped":
        return ["QA ⚠️ skipped (--no-qa): no automatic checks"]
    failures = json.loads(reel["qa_failures"] or "[]")
    lines = ["QA ✅" if status == "pass" else f"QA ❌ {len(failures)} failure(s):"]
    lines += [f"  - {f}" for f in failures]
    lines += [f"⚠️ {w}" for w in json.loads(reel["qa_warnings"] or "[]")]
    path = stored_path(reel["qa_path"]) if reel["qa_path"] else None
    if path is not None and path.is_file():
        lines += json.loads(path.read_text(encoding="utf-8")).get("summary", [])
    return lines


def version_lines(reel: sqlite3.Row) -> list[str]:
    """The status block's line on the platform versions (reelline/media/share.py, the render's
    versions.json): each platform's own share arrow, the video above being the review
    platform's; [] for a reel rendered before them."""

    path = stored_path(reel["mp4_path"]).parent / "versions.json" if reel["mp4_path"] else None
    if path is None or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    names = [conf().publish.labels.get(p, p) for p in data.get("files", {})]
    if not data.get("arrow"):
        return [f"📱 {len(names)} platform versions, all the same file (no share arrow: no share line)"]
    review = conf().publish.labels.get(data.get("review"), data.get("review"))
    share = data.get("share") or {}
    return [
        f"📱 {len(names)} platform versions, each with its share arrow at "
        f"{share.get('start', 0):.1f}-{share.get('end', 0):.1f}s: {', '.join(names)} (the video above is {review}'s)"
    ]


def gag_lines(reel: sqlite3.Row) -> list[str]:
    """The status block's line on the cold open (reelline/media/gags.py, reels.gag / gag_s / gag_mode); [] for a
    reel from before it or one rendered with the cold open switched off (gag_mode off: the straight style,
    reelline/media/style.py, or gags.json mode off)."""
    mode = reel["gag_mode"]
    if mode is None or (mode == "off" and not reel["gag"]):
        return []
    how = {
        "ab": "A/B, the {arm} arm",
        "on": "every reel: gags.json mode on",
        "off": "gags.json mode off",
        "forced": "forced with --gag",
    }.get(mode, mode).format(arm="gag" if reel["gag"] else "no-gag")
    if reel["gag"]:
        return [f"🎬 Cold open: {reel['gag']}, {reel['gag_s'] or 0:.1f}s before the reel ({how})"]
    return [f"🎬 Cold open: none ({how})"]


def status_block(conn: sqlite3.Connection, reel: sqlite3.Row) -> str:
    script = conn.execute("SELECT * FROM scripts WHERE id = ?", (reel["script_id"],)).fetchone()
    pick = conn.execute("SELECT * FROM picks WHERE id = ?", (reel["pick_id"],)).fetchone()
    lines = [
        about_reel(
            reel["id"],
            f"{reel['status']}, script #{script['id']}, pick #{pick['id']} ({pick['slot_date']} {pick['slot_time']})",
        )
    ]
    if reel["status"] == "render_failed":
        lines.append(f"Render failed after {reel['attempts']} attempt(s): {short_error(reel['error'] or '')}")

    failures = json.loads(script["failures"] or "[]")
    if script["status"] == "flagged" or failures:
        lines.append(f"Fact-check: ⚠️ {len(failures)} failed claim(s)")
        lines += [f"  - {f}" for f in failures]
    else:
        rounds = json.loads(script["factcheck"] or "{}").get("rounds", [])
        counts = rounds[-1]["counts"] if rounds else {}
        definition = counts.get("general_definition", 0)
        lines.append(
            f"Fact-check: ✅ {counts.get('supported', 0)} claim(s) supported"
            + (f", {definition} general definition" if definition else "")
        )

    lock = json.loads(script["draft_lock"] or "{}")
    changed, dropped = lock.get("changed", []), lock.get("dropped", [])
    if changed or dropped:
        lines.append(f"Draft lock: ⚠️ {len(changed)} changed, {len(dropped)} dropped")
        lines += [f"  changed: {c['draft']} -> {c['final']}" for c in changed]
        lines += [f"  dropped: {d}" for d in dropped]

    from reelline.pipeline.endcard import summary_lines as card_lines
    from reelline.pipeline.proofread import status_lines

    lines += status_lines(conn, script["id"])
    lines += card_lines(json.loads(script["passes"] or "{}").get("card"))
    from reelline.pipeline.fx import summary_lines as fx_lines

    events = stored_path(reel["mp4_path"]) if reel["mp4_path"] else None
    events = events.parent / "fx_events.json" if events else None
    lines += fx_lines(
        json.loads(script["passes"] or "{}").get("fx"),
        json.loads(events.read_text(encoding="utf-8")) if events and events.is_file() else None,
    )
    if pick["flirt"] is not None:
        angle = f" (angle: {pick['flirt_angle']})" if pick["flirt"] and pick["flirt_angle"] else ""
        lines.append(f"💘 flirt line: {'yes' if pick['flirt'] else 'no'}{angle}")
    lines += version_lines(reel)
    lines += gag_lines(reel)
    lines += qa_lines(reel)
    # With QA the low-score words are among its warnings (after the re-roll); without, listed here.
    check = json.loads(reel["check_these"] or "[]")
    if reel["status"] != "render_failed" and reel["qa_status"] in (None, "skipped"):
        lines.append(f"Check these ({len(check)}):" if check else "Check these: none")
        lines += [f"  ¶{r['paragraph']} {r['start']:.1f}s {r['word']} ({r['score']:.2f})" for r in check]

    from reelline.pipeline import shortlinks

    for link in shortlinks.reel_links(conn, reel["id"]):
        lines.append(
            f"Link: {shortlinks.caption_url(link['code'])} -> {link['target']}"
            + ("" if link["verified_at"] else " (live once posted)")
        )
    duration = f", {reel['duration_s']:.1f}s" if reel["duration_s"] is not None else ""
    lines.append(f"Background: {reel['background']} ({reel['background_source']}), mood {reel['mood']}{duration}")
    lines.append(cost_line(conn, reel["id"]))
    return "\n".join(lines)


def reel_report(conn: sqlite3.Connection, reel_id: int) -> str:
    reel = conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()
    if reel is None:
        raise ProduceError(f"No reel #{reel_id}")
    out = [
        f"Reel #{reel['id']}: {reel['name']} ({reel['status']}), {reel['attempts']} attempt(s), "
        f"render {reel['render_s']:.0f}s, {reel['created_at']}"
    ]
    for label, key in (
        ("MP4", "mp4_path"),
        ("Cover", "cover_path"),
        ("Images", "images_path"),
        ("Attributions", "attributions_path"),
    ):
        out.append(f"  {label}: {reel[key] or '-'}")
    if reel["error"]:
        out.append(f"  Error: {reel['error']}")
    out.append("\n== Caption\n" + (reel["caption"] or "(none)"))
    out.append("\n== Status\n" + status_block(conn, reel))
    return "\n".join(out)
