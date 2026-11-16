"""The QA report: qa_<reel id>.json, the chat text and the cost records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from reelline.pipeline.qa.base import QA, Report
from reelline.pipeline.qa.rerolls import Outcome


def summary_lines(history: dict) -> list[str]:
    """Review lines for the re-rolls and re-renders."""
    lines = []
    for entry in history.get("rerolls", []):
        if entry.get("failed"):
            lines.append(
                f"Re-roll of {', '.join(f'¶{n}' for n in entry['failed'])} failed, old takes kept: "
                f"{entry['error'][:120]}"
            )
            continue
        parts = [
            f"¶{p['paragraph']} kept {p['kept']} ({p['old']['low_words']} -> {p['new']['low_words']} low)"
            for p in entry["paragraphs"]
        ]
        lines.append("Re-roll: " + ", ".join(parts))
    reruns = [r for r in history.get("renders", []) if r["reason"].startswith("QA failed")]
    for r in reruns:
        lines.append(f"QA re-render after: {r['reason'].removeprefix('QA failed: ')}")
    return lines


def qa_json(outcome: Outcome) -> dict:
    h = outcome.history
    reports = [rep.as_json() for rep in h["reports"]]
    return {
        "status": outcome.report.status if outcome.report else None,
        "failures": outcome.report.failures if outcome.report else [],
        "warnings": outcome.report.warnings if outcome.report else [],
        "thresholds": {k: (str(v) if isinstance(v, Path) else v) for k, v in QA.items()},
        "rerolls": h["rerolls"],
        "renders": h["renders"],
        "summary": summary_lines(h),
        "reports": reports,
        "cost_usd": {
            "qa_vision": round(sum(r["vision_cost_usd"] for r in reports), 6),
            "images": round(sum(c["cost_usd"] for c in outcome.calls if c["purpose"].startswith("image")), 6),
            "tts_new": round(outcome.tts_new_cost_usd, 6),
            # An illustration priced without the usage's modality breakdown.
            "images_upper_bound": any(c["purpose"] == "image_generate_upper_bound" for c in outcome.calls),
        },
    }


def write_qa_json(reel_dir: Path, data: dict, reel_id: int | None = None) -> Path:
    """qa_<reel id>.json in the reel dir: reels of the same script share the dir (and its
    TTS cache), so each keeps its own report. qa.json without a reel id."""
    path = reel_dir / (f"qa_{reel_id}.json" if reel_id is not None else "qa.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


MARK = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "–"}


def report_text(report: Report) -> str:
    out = []
    for c in report.checks:
        value = c.value if not isinstance(c.value, (dict, list)) else json.dumps(c.value, ensure_ascii=False)
        out.append(f"{MARK[c.result]} {c.group:<9} {c.name:<20} {value}")
        out += [f"      {line}" for line in c.lines]
    out.append("")
    out.append("QA ✅" if report.status == "pass" else f"QA ❌ {len(report.failures)} failure(s)")
    out += [f"  - {f}" for f in report.failures]
    out += [f"⚠️ {w}" for w in report.warnings]
    out.append(f"Vision: {len(report.calls)} new call(s), ${report.cost_usd:.4f}")
    return "\n".join(out)


def script_for_reel(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM scripts WHERE path LIKE ? ORDER BY id DESC LIMIT 1", (f"%/{name}.txt",)
    ).fetchone()


def caption_fields(
    conn: sqlite3.Connection,
    script_id: int,
    source_names: dict[str, str],
    caption: str | None = None,
    reel_id: int | None = None,
) -> dict:
    """Subject caption fields for a script: the assembled caption (or `caption`), title,
    hashtags, the story's URL and the reel id (the credits line's)."""
    from reelline.pipeline import db
    from reelline.pipeline.caption import assemble_caption

    script = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
    cap = conn.execute("SELECT * FROM captions WHERE script_id = ? ORDER BY id DESC LIMIT 1", (script_id,)).fetchone()
    story = db.get_story(conn, script["story_id"])
    return {
        "caption": caption if caption is not None else assemble_caption(conn, script_id, source_names, reel_id),
        "youtube_title": cap["youtube_title"] if cap else None,
        "hashtags": json.loads(cap["hashtags"]) if cap else None,
        "source_url": story["canonical_url"] if story else None,
        "reel_id": reel_id,
    }


def record_calls(
    conn: sqlite3.Connection,
    calls: list[dict],
    script_id: int | None,
    reel_id: int | None = None,
    manual: bool | None = None,
) -> None:
    """Paid model calls (reelline/media/images/vision.py call_row(): image checks, generated
    illustrations, QA frames) into llm_calls, with the reel's script (reelline/pipeline/db.py
    record_calls())."""
    from reelline.pipeline import db

    db.record_calls(conn, calls, script_id=script_id, reel_id=reel_id, manual=manual)
