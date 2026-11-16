"""Retention, kept permanently: what the stats snapshots (reelline/pipeline/stats.py) learn about
where the audience leaves, a Telegram line per reel, and a weekly winners / flops file for
the script prompt. Read-only: nothing is posted or changed on a platform, and no table of
its own: the numbers live in post_stats.raw.

What a snapshot keeps (post_stats.raw, JSON per source):
  youtube_retention  YouTube's curve, from the +72 h snapshot on (steps.retention.min_curve_target_h):
                     {"curve": [[elapsed ratio, audienceWatchRatio, relativeRetentionPerformance], ...],
                     "half_gone_s": s | null}; absent when YouTube has too few views to give one
  instagram_graph    reels_skip_rate: the percent of initial views that skipped in the first 3 s
Average watch time and % viewed are the snapshot's own columns (avg_watch_s, avg_pct; TikTok's
and a missing % are derived from the reel's length, as the reports do).

Per reel (reel_metrics()):
  half_gone_s   YouTube: the first time the audienceWatchRatio is under steps.retention.half_gone_share,
                interpolated between the curve's points; null without a curve or when the audience
                never gets that low
  hook_s        the opening paragraph's length: from its first word to the second paragraph's first
                word (the paragraph's last word without a second one); from the script and the
                reel's out/.../letters.json (null where either is gone)
  per platform  the latest views, avg_watch_s, avg_pct and (Instagram) skip_pct

The line (once per reel, a tick after its last publication's +72 h snapshot landed):
  Reel #N · YT 50% gone at X s · TT avg Y s (Z%) · IG avg Y s, skip Z% · hook H s · gag G
A part with no number is left out; a reel with no platform number gets no line. The last part is the
reel's cold open (reels.gag: "gag <name>", or "no gag"; none for a reel from before the cold open or
rendered with it switched off: gag_mode off, the straight style or gags.json mode off). The
times are the video's: a reel with a cold open has it before its first word (reels.gag_s).

data/hook_examples.json (the config's storage.hook_examples, read by the story prompt),
rewritten once a week, Tbilisi steps.retention.examples_weekday from steps.retention.examples_at
(the tick catches a missed one up):
  {"updated": iso, "winners": [{"reel_id", "opening", "half_gone_s", "avg_pct_viewed"}], "flops": [same]}
Reels first published in the last examples_days, last publication at least examples_min_age_h old,
with examples_min_views views on the platforms that count (examples_platform_min_views each),
ranked by (1 - w) * % viewed (views-weighted over those platforms, each capped at 100: replays
count in Meta's watch time) + w * half_gone_s as % of the reel's length. Winners are the top
examples_top, best first; flops the bottom ones, worst first (fewer each when there are fewer than
twice as many reels). "opening" is the first sentence of paragraph 1, display text.

Usage:
  python -m reelline.pipeline.retention reel <reel_id>     the line for a reel (printed, not sent)
  python -m reelline.pipeline.retention examples [--write] the ranking (and write the file now)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from reelline.config import conf
from reelline.paths import REPO_ROOT
from reelline.pipeline import db
from reelline.pipeline.scoring import tbilisi

SENT_KEY = "retention_line:{}"
EXAMPLES_KEY = "retention_examples_week"


# ---- The curve ------------------------------------------------------------------------------------


def wanted(target_h: float | None) -> bool:
    """Whether a snapshot at this point (post_stats.target_h; None: no point) fetches the curve."""
    return target_h is not None and target_h >= conf().steps.retention.min_curve_target_h


def half_gone_s(curve: list, duration_s: float | None) -> float | None:
    """When the audienceWatchRatio first falls under steps.retention.half_gone_share, in seconds of
    the video: the elapsed ratio interpolated between the point before and the one under it."""
    if not curve or not duration_s:
        return None
    level = conf().steps.retention.half_gone_share
    prev_ratio, prev_watch = 0.0, None
    for ratio, watch, *_ in curve:
        if watch is None:
            continue
        if watch < level:
            if prev_watch is None or prev_watch <= watch:
                at = ratio
            else:
                at = prev_ratio + (ratio - prev_ratio) * (prev_watch - level) / (prev_watch - watch)
            return round(at * duration_s, 2)
        prev_ratio, prev_watch = ratio, watch
    return None


def curve_raw(curve: list, duration_s: float | None) -> dict:
    """The curve as post_stats.raw keeps it (YouTubeClient.retention() rows)."""
    return {
        "curve": [[round(float(x), 4) if x is not None else None for x in row] for row in curve],
        "half_gone_s": half_gone_s(curve, duration_s),
    }


# ---- The script's opening --------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?…][“”\"»])\s+|(?<=[.!?…])\s+")


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    return _SENTENCE_END.split(text, 1)[0] if text else ""


def _plain_opening(source: str) -> str | None:
    """Paragraph 1's display text straight from the markup, for a script the parser no longer
    takes: the first non-directive paragraph, {display|spoken} -> display, vocal tags dropped."""
    for block in re.split(r"\n\s*\n", source.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip() and not ln.lstrip().startswith("@")]
        if not lines:
            continue
        text = " ".join(lines)
        text = re.sub(r"\{([^{}|]*)\|[^{}]*\}", r"\1", text)
        text = re.sub(r"<[^<>]*>", "", text)
        return first_sentence(text) or None
    return None


def opening_of(source: str) -> tuple[str | None, object | None]:
    """(the first sentence of paragraph 1, display text; the parsed Script or None)."""
    from reelline.media import script_markup

    script = None
    for require_tag in (True, False):
        try:
            script = script_markup.parse(source, require_tag=require_tag)
            break
        except Exception:  # an old markup: the plain way below
            continue
    if script is not None and script.paragraphs:
        sentence = first_sentence(script.paragraphs[0].display)
        if sentence:
            return sentence, script
    return _plain_opening(source), script


def hook_seconds(script, letters: list[dict]) -> float | None:
    """Paragraph 1's length: its first timed word to paragraph 2's first timed word (to its own
    last word's end when it is the only paragraph)."""
    from reelline.media import script_markup

    try:
        timed = script_markup.time_tokens(script, letters)
    except Exception:
        return None
    first = [t for tok, t in zip(script.tokens, timed, strict=False) if tok.paragraph == 0 and t["start"] is not None]
    if not first:
        return None
    second = next(
        (
            t["start"]
            for tok, t in zip(script.tokens, timed, strict=False)
            if tok.paragraph == 1 and t["start"] is not None
        ),
        None,
    )
    end = second if second is not None else max(t["end"] for t in first)
    return round(end - first[0]["start"], 2)


def hook_info(conn: sqlite3.Connection, reel_id: int, root: Path = REPO_ROOT) -> tuple[str | None, float | None]:
    """(opening sentence, hook seconds) of a reel; None for what is gone."""
    row = conn.execute(
        """SELECT r.mp4_path, s.final, s.draft FROM reels r JOIN scripts s ON s.id = r.script_id
                          WHERE r.id = ?""",
        (reel_id,),
    ).fetchone()
    source = (row["final"] or row["draft"]) if row else None
    if not source:
        return None, None
    opening, script = opening_of(source)
    hook = None
    letters_file = (root / row["mp4_path"]).parent / "letters.json" if row["mp4_path"] else None
    if script is not None and letters_file is not None and letters_file.exists():
        try:
            hook = hook_seconds(script, json.loads(letters_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            hook = None
    return opening, hook


# ---- A reel's numbers ------------------------------------------------------------------------------


def reel_metrics(conn: sqlite3.Connection, reel_id: int, root: Path = REPO_ROOT, hook: bool = True) -> dict:
    """{"reel_id", "duration_s", "half_gone_s", "hook_s", "opening", "gag", "gag_mode", "platforms": {channel:
    {"views", "avg_watch_s", "avg_pct", "pct_derived", "skip_pct"}}} from the latest snapshots; gag and gag_mode
    the reel's cold open (reels.gag, gag_mode; reelline/media/gags.py), None before it."""
    reel = conn.execute("SELECT duration_s, gag, gag_mode FROM reels WHERE id = ?", (reel_id,)).fetchone()
    duration = reel["duration_s"] if reel else None
    out = {
        "reel_id": reel_id,
        "duration_s": duration,
        "half_gone_s": None,
        "hook_s": None,
        "opening": None,
        "gag": reel["gag"] if reel else None,
        "gag_mode": reel["gag_mode"] if reel else None,
        "platforms": {},
    }
    pubs = conn.execute(
        """SELECT id, channel FROM publications WHERE reel_id = ? AND state = 'published'
                           AND published_at IS NOT NULL ORDER BY id""",
        (reel_id,),
    ).fetchall()
    for pub in pubs:
        platform = out["platforms"].setdefault(
            pub["channel"],
            {"views": None, "avg_watch_s": None, "avg_pct": None, "pct_derived": False, "skip_pct": None},
        )
        rows = conn.execute(
            "SELECT * FROM post_stats WHERE publication_id = ? ORDER BY as_of, fetched_at, id", (pub["id"],)
        ).fetchall()
        for key in ("views", "avg_watch_s"):
            platform[key] = next((r[key] for r in reversed(rows) if r[key] is not None), platform[key])
        for r in reversed(rows):
            if r["avg_pct"] is not None:
                platform["avg_pct"], platform["pct_derived"] = r["avg_pct"], bool(r["pct_derived"])
                break
        for r in reversed(rows):
            raw = _raw(r)
            if (
                pub["channel"] == "instagram"
                and platform["skip_pct"] is None
                and (raw.get("instagram_graph") or {}).get("reels_skip_rate") is not None
            ):
                platform["skip_pct"] = float(raw["instagram_graph"]["reels_skip_rate"])
            if pub["channel"] == "youtube" and out["half_gone_s"] is None and raw.get("youtube_retention"):
                curve = raw["youtube_retention"].get("curve")
                out["half_gone_s"] = (
                    half_gone_s(curve, duration) if duration else raw["youtube_retention"].get("half_gone_s")
                )
    if hook:
        out["opening"], out["hook_s"] = hook_info(conn, reel_id, root)
    return out


def _raw(row: sqlite3.Row) -> dict:
    try:
        raw = json.loads(row["raw"]) if row["raw"] else {}
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


# ---- The Telegram line ------------------------------------------------------------------------------


def _s(x: float) -> str:
    return f"{x:.1f} s"


def line_text(m: dict) -> str | None:
    """The reel's line; None when no platform has a number to show."""
    short = conf().steps.analytics.short
    parts = []
    p = m["platforms"]
    if m["half_gone_s"] is not None:
        parts.append(
            f"{short['youtube']} {conf().steps.retention.half_gone_share * 100:.0f}% gone at {_s(m['half_gone_s'])}"
        )
    tt = p.get("tiktok")
    if tt and tt["avg_watch_s"]:  # Buffer gives 0 until TikTok has counted
        parts.append(
            f"{short['tiktok']} avg {_s(tt['avg_watch_s'])}"
            + (f" ({tt['avg_pct']:.0f}%)" if tt["avg_pct"] is not None else "")
        )
    ig = p.get("instagram")
    if ig and (ig["avg_watch_s"] or ig["skip_pct"] is not None):
        bits = ([f"avg {_s(ig['avg_watch_s'])}"] if ig["avg_watch_s"] else []) + (
            [f"skip {ig['skip_pct']:.0f}%"] if ig["skip_pct"] is not None else []
        )
        parts.append(f"{short['instagram']} {', '.join(bits)}")
    if not parts:
        return None
    if m["hook_s"] is not None:
        parts.append(f"hook {_s(m['hook_s'])}")
    if m.get("gag_mode") and (m["gag_mode"] != "off" or m.get("gag")):  # the cold open (reelline/media/gags.py)
        parts.append(f"gag {m['gag']}" if m.get("gag") else "no gag")
    return " · ".join([f"Reel #{m['reel_id']}"] + parts)


def lines_due(conn: sqlite3.Connection, now: datetime) -> list[int]:
    """The reels whose line is due: their last publication's +72 h point reached (not longer ago
    than line_window_h), every published channel with a snapshot from +72 h on or line_grace_h
    passed, and not sent yet."""
    after = timedelta(hours=conf().steps.retention.line_after_h)
    due = []
    reels = conn.execute("""SELECT reel_id, MAX(published_at) AS last FROM publications
                            WHERE state = 'published' AND published_at IS NOT NULL GROUP BY reel_id
                            ORDER BY reel_id""").fetchall()
    for r in reels:
        ready = datetime.fromisoformat(r["last"]) + after
        if not ready <= now < ready + timedelta(hours=conf().steps.retention.line_window_h):
            continue
        if db.get_state(conn, SENT_KEY.format(r["reel_id"])):
            continue
        missing = conn.execute(
            """SELECT COUNT(*) FROM publications p WHERE p.reel_id = ? AND p.state = 'published'
                                  AND p.published_at IS NOT NULL AND NOT EXISTS (
                                    SELECT 1 FROM post_stats s WHERE s.publication_id = p.id AND s.target_h >= ?)""",
            (r["reel_id"], conf().steps.retention.line_after_h),
        ).fetchone()[0]
        if missing and now < ready + timedelta(hours=conf().steps.retention.line_grace_h):
            continue
        due.append(r["reel_id"])
    return due


def send_lines(
    conn: sqlite3.Connection,
    now: datetime,
    say: Callable[[str], None],
    log: Callable[[str], None],
    root: Path = REPO_ROOT,
) -> list[int]:
    """Say the due lines (one message each), mark them sent. Returns the reels sent."""
    sent = []
    for reel_id in lines_due(conn, now):
        text = line_text(reel_metrics(conn, reel_id, root))
        if text is None:
            continue
        say(text)
        db.set_state(conn, SENT_KEY.format(reel_id), db.now_iso(now))
        log(f"retention: {text}")
        sent.append(reel_id)
    return sent


# ---- The weekly examples ----------------------------------------------------------------------------


def _week(local: datetime) -> str:
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def examples_due(conn: sqlite3.Connection, now: datetime) -> bool:
    """This Tbilisi week's weekday + time reached (or passed, in a later weekday) and not written this week."""
    local = now.astimezone(tbilisi())
    h, m = (int(x) for x in conf().steps.retention.examples_at.split(":"))
    reached = (local.weekday(), local.hour, local.minute) >= (conf().steps.retention.examples_weekday, h, m)
    return reached and db.get_state(conn, EXAMPLES_KEY) != _week(local)


def score(m: dict) -> tuple[float | None, float | None]:
    """(combined score, views-weighted % viewed) of a reel's metrics; (None, None) when fewer than
    examples_min_views views are on platforms that count."""
    num = den = 0.0
    for p in m["platforms"].values():
        if (
            p["views"] is not None
            and p["views"] >= conf().steps.retention.examples_platform_min_views
            and p["avg_pct"] is not None
        ):
            num += min(p["avg_pct"], 100.0) * p["views"]
            den += p["views"]
    if den < conf().steps.retention.examples_min_views:
        return None, None
    pct = num / den
    w = conf().steps.retention.examples_half_gone_weight
    if m["half_gone_s"] is not None and m["duration_s"]:
        return (1 - w) * pct + w * m["half_gone_s"] / m["duration_s"] * 100, pct
    return pct, pct


def rank_examples(conn: sqlite3.Connection, now: datetime, root: Path = REPO_ROOT) -> dict:
    """{"winners": [...], "flops": [...]} entries as the file has them (the contract above)."""
    first_after = now - timedelta(days=conf().steps.retention.examples_days)
    last_before = now - timedelta(hours=conf().steps.retention.examples_min_age_h)
    scored = []
    for r in conn.execute("""SELECT reel_id, MIN(published_at) AS first, MAX(published_at) AS last FROM publications
                             WHERE state = 'published' AND published_at IS NOT NULL GROUP BY reel_id""").fetchall():
        if datetime.fromisoformat(r["first"]) < first_after or datetime.fromisoformat(r["last"]) > last_before:
            continue
        m = reel_metrics(conn, r["reel_id"], root)
        total, pct = score(m)
        if total is None or not m["opening"]:
            continue
        scored.append(
            (
                total,
                r["reel_id"],
                {
                    "reel_id": r["reel_id"],
                    "opening": m["opening"],
                    "half_gone_s": None if m["half_gone_s"] is None else round(m["half_gone_s"], 1),
                    "avg_pct_viewed": round(pct, 1),
                },
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1]))
    n = min(conf().steps.retention.examples_top, len(scored) // 2)
    return {"winners": [e for _, _, e in scored[:n]], "flops": [e for _, _, e in reversed(scored[len(scored) - n :])]}


def write_examples(conn: sqlite3.Connection, now: datetime, path: Path | None = None, root: Path = REPO_ROOT) -> dict:
    """Rank and write the file atomically (a temp file, then a rename); the week is marked done."""
    path = Path(path or conf().storage.hook_examples)
    data = {"updated": db.now_iso(now), **rank_examples(conn, now, root)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    db.set_state(conn, EXAMPLES_KEY, _week(now.astimezone(tbilisi())))
    return data


# ---- The tick's part ---------------------------------------------------------------------------------


def tick(
    conn: sqlite3.Connection,
    now: datetime,
    say: Callable[[str], None],
    log: Callable[[str], None] = print,
    root: Path = REPO_ROOT,
    path: Path | None = None,
) -> str | None:
    """The due lines, and the week's examples file. Returns what it did, None for nothing."""
    did = []
    sent = send_lines(conn, now, say, log, root)
    if sent:
        did.append(f"retention lines for reels {', '.join(f'#{i}' for i in sent)}")
    if examples_due(conn, now):
        data = write_examples(conn, now, path, root)
        did.append(f"hook examples: {len(data['winners'])} winners, {len(data['flops'])} flops")
    return "; ".join(did) or None


# ---- CLI ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m reelline.pipeline.retention", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("reel", help="a reel's line, printed")
    one.add_argument("reel_id", type=int)
    ex = sub.add_parser("examples", help="the winners and flops, printed")
    ex.add_argument("--write", action="store_true", help="write the file now (and count the week as done)")
    args = ap.parse_args(argv)
    conn = db.connect(conf().storage.db_path)
    now = datetime.now(UTC)
    if args.cmd == "reel":
        m = reel_metrics(conn, args.reel_id)
        print(line_text(m) or "(no retention numbers yet)")
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        data = write_examples(conn, now) if args.write else {"updated": db.now_iso(now), **rank_examples(conn, now)}
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
