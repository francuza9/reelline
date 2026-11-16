"""The cooldown: images shown in recent reels aren't picked again for a while.

recently_shown() reads the reels table (reelline/pipeline/db.py) for the last COOLDOWN["reels"]
reels whose status is in COOLDOWN["statuses"] (approved or published), newest first by
creation time, not counting the reel being rendered (any row with its name), and
collects the image ids their images.json shows. resolve.resolve_reel() keeps those out
of every search except a logo query for an organization the story names (its own logo
may show again).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from reelline.media.images.config import COOLDOWN


def recently_shown(
    conn: sqlite3.Connection, root: Path, reel_name: str, before: str | None = None, log: Callable[[str], None] = print
) -> set[str]:
    """Image ids shown in the recent reels; `before` (an ISO time) counts only reels made
    before it (a look back from an older reel). images_path is relative to `root`."""
    statuses = COOLDOWN["statuses"]
    sql = f"SELECT name, images_path FROM reels WHERE status IN ({', '.join('?' * len(statuses))}) AND name != ?"
    args: list = [*statuses, reel_name]
    if before is not None:
        sql += " AND created_at < ?"
        args.append(before)
    names: list[str] = []
    shown: set[str] = set()
    for name, images_path in conn.execute(sql + " ORDER BY created_at DESC, id DESC", args):
        if name in names:
            continue
        if len(names) >= COOLDOWN["reels"]:
            break
        names.append(name)
        path = root / images_path if images_path else None
        if path is None or not path.is_file():
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))["paragraphs"]
        except (ValueError, KeyError, TypeError) as exc:
            log(f"WARNING: cooldown: {path} unreadable ({type(exc).__name__}); its images may repeat")
            continue
        shown |= {e["image_id"] for e in entries if e.get("status") == "image" and e.get("image_id")}
    return shown


def recently_shown_in(db_path: Path, root: Path, reel_name: str, log: Callable[[str], None] = print) -> set[str]:
    """recently_shown() from the database file, opened read-only; empty (with a note)
    without one (a dev checkout)."""
    if not db_path.is_file():
        log(f"NOTE: no database at {db_path}; no image cooldown")
        return set()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return recently_shown(conn, root, reel_name, log=log)
    finally:
        conn.close()
