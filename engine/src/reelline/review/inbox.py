"""The review inbox: decisions dropped as files by `reelline review tap|say` (reelline/cli.py), taken in
order by the cli, webhook and auto channels' receive(). One JSON file per decision under
review.cli.inbox_dir: {"type": "tap", "data": "r:12:ap", "user": "..."} or {"type": "say", "text":
"/status", "user": "..."}; written atomically (a temp file renamed), deleted once read.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from reelline.config import conf
from reelline.review.base import Event, Incoming, Tap, parse_command


def inbox_dir() -> Path:
    return Path(conf().review.cli.inbox_dir)


def drop(entry: dict, where: Path | None = None) -> Path:
    where = where or inbox_dir()
    where.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns():020d}_{os.getpid()}"
    tmp = where / f".{name}.tmp"
    tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    return tmp.replace(where / f"{name}.json")


def event_of(entry: dict) -> Event | None:
    user = str(entry.get("user") or "cli")
    if entry.get("type") == "tap" and entry.get("data"):
        return Tap(str(entry["data"]), user)
    if entry.get("type") == "say":
        return parse_command(str(entry.get("text") or ""), user)
    return None


def take(where: Path | None = None) -> list[Incoming]:
    """The inbox's decisions, oldest first, each removed as it is read (an unreadable one: ignored)."""
    where = where or inbox_dir()
    if not where.is_dir():
        return []
    out = []
    for path in sorted(where.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entry = None
        path.unlink(missing_ok=True)
        event = event_of(entry) if isinstance(entry, dict) else None
        out.append(
            Incoming(path.stem, [event] if event else [], None, ignored=None if event else f"inbox file {path.name}")
        )
    return out
