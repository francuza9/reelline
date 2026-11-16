"""Leak guard: what must not be in the open-source engine, and the list of known exceptions.

Scans the files git knows under engine/ (git ls-files; every file when there is no git) for:

    name        the first channel's names (the channel, its handle, its Georgian name)
    pages-dev   the channel's site on Cloudflare Pages
    server      the production host, its user and home paths
    email       an email address (example/noreply addresses are fine)
    phone       a phone number
    chat-id     a Telegram chat id (a supergroup id, or chat_id set to a number)
    private-url a URL on a private network, a tunnel, or the bucket's own endpoint
    big-file    a file over 5 MB

Each hit must be covered by an entry of engine/leak_guard_allow.toml: a path glob, the rules it excuses,
why, and the phase that removes it. `max_hits` (optional) caps the hits the entry excuses, so an excused
file can't grow new leaks. An entry that excuses nothing is an error too (the allowlist only shrinks).

    reelline dev leak-guard [--root DIR] [--allowlist FILE] [--summary]
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024

# The patterns are built from pieces (the Georgian spelling as escapes) so this file does not match its own rules.
_NAME = "y" + "vito"
RULES: dict[str, re.Pattern[str]] = {
    "name": re.compile(rf"{_NAME}|" + "\u10d8\u10e7\u10d5\u10d8\u10d7\u10dd", re.IGNORECASE),
    "pages-dev": re.compile(r"[\w-]+\.pages\.dev", re.IGNORECASE),
    "server": re.compile(r"het" + "zner|/home/\\w+|~/" + _NAME, re.IGNORECASE),
    "email": re.compile(
        r"\b(?!noreply@)[\w.+-]+@(?!example\.|users\.noreply\.)[\w-]+(?:\.[\w-]+)*\.(?!service\b)[a-z]{2,}\b",
        re.IGNORECASE,
    ),
    "phone": re.compile(r"(?<![\w.])\+\d{10,15}\b|(?<![\w.])\+\d{2,3}[ -]\d{3}[ -]\d{2,3}[ -]\d{2,3}\b"),
    "chat-id": re.compile(r"(?<![\w.])-100\d{8,}\b|chat_?id[\"']?\s*[:=]\s*[\"']?-?\d{6,}", re.IGNORECASE),
    "private-url": re.compile(
        r"https?://(?:[\w.-]*\.(?:internal|local|ts\.net|ngrok[\w.-]*|trycloudflare\.com|r2\.cloudflarestorage\.com)"
        r"|(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.[\d.]+)\b",
        re.IGNORECASE,
    ),
}
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
ALLOWLIST = Path(__file__).resolve().parents[3] / "leak_guard_allow.toml"


@dataclass(frozen=True)
class Hit:
    path: str  # relative to the root, with "/"
    line: int  # 0 for a whole-file rule
    rule: str
    text: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.rule}: {self.text}"


@dataclass
class Entry:
    path: str
    rules: list[str]
    reason: str
    phase: str
    max_hits: int | None = None
    used: int = 0

    def covers(self, hit: Hit) -> bool:
        return (self.rules == ["*"] or hit.rule in self.rules) and fnmatch.fnmatchcase(hit.path, self.path)


@dataclass
class Report:
    leaks: list[Hit] = field(default_factory=list)  # hits no entry excuses
    unused: list[Entry] = field(default_factory=list)  # entries that excuse nothing
    over: list[Entry] = field(default_factory=list)  # entries over their max_hits
    allowed: Counter[str] = field(default_factory=Counter)  # hits excused, by entry path

    @property
    def ok(self) -> bool:
        return not (self.leaks or self.unused or self.over)


def load_allowlist(path: Path) -> list[Entry]:
    entries = []
    for i, raw in enumerate(tomllib.loads(path.read_text(encoding="utf-8")).get("allow", []), 1):
        missing = [k for k in ("path", "rules", "reason", "phase") if not raw.get(k)]
        if missing:
            raise ValueError(f"{path.name}: entry {i} ({raw.get('path', '?')}) lacks {', '.join(missing)}")
        unknown = [r for r in raw["rules"] if r != "*" and r not in RULES and r != "big-file"]
        if unknown:
            raise ValueError(f"{path.name}: entry {i} ({raw['path']}) has unknown rules {unknown}")
        entries.append(Entry(raw["path"], list(raw["rules"]), raw["reason"], raw["phase"], raw.get("max_hits")))
    return entries


def files(root: Path) -> list[Path]:
    """The tracked and not-ignored files under root (every file when root is not in a git work tree)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        listed = [root / p for p in out.split("\0") if p]
        if listed:
            return [p for p in listed if p.is_file()]
    except (OSError, subprocess.CalledProcessError):
        pass
    return sorted(p for p in root.rglob("*") if p.is_file() and not SKIP_DIRS.intersection(p.relative_to(root).parts))


def scan(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for p in files(root):
        rel = p.relative_to(root).as_posix()
        if p.stat().st_size > MAX_BYTES:
            hits.append(Hit(rel, 0, "big-file", f"{p.stat().st_size / 1e6:.1f} MB"))
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # a binary file: only its size matters
        for n, line in enumerate(text.splitlines(), 1):
            for rule, pattern in RULES.items():
                if (m := pattern.search(line)) is not None:
                    hits.append(Hit(rel, n, rule, m.group(0)))
    return hits


def check(root: Path, allowlist: Path = ALLOWLIST) -> Report:
    entries = load_allowlist(allowlist)
    report = Report()
    for hit in scan(root):
        entry = next((e for e in entries if e.covers(hit)), None)
        if entry is None:
            report.leaks.append(hit)
            continue
        entry.used += 1
        report.allowed[entry.path] += 1
    for e in entries:
        if e.used == 0:
            report.unused.append(e)
        elif e.max_hits is not None and e.used > e.max_hits:
            report.over.append(e)
    return report


def main(root: Path, allowlist: Path = ALLOWLIST, summary: bool = False) -> int:
    report = check(root, allowlist)
    for hit in report.leaks:
        print(f"LEAK {hit}")
    for e in report.unused:
        print(f"UNUSED allowlist entry {e.path} ({', '.join(e.rules)}): it excuses nothing; remove it ({e.phase})")
    for e in report.over:
        print(f"OVER allowlist entry {e.path}: {e.used} hits, at most {e.max_hits} excused")
    if summary or report.ok:
        print(f"allowlisted: {sum(report.allowed.values())} hits in {len(report.allowed)} entries")
        for path, n in report.allowed.most_common(10 if not summary else None):
            print(f"  {n:5}  {path}")
    print("leak guard: " + ("ok" if report.ok else "FAILED"))
    return 0 if report.ok else 1


if __name__ == "__main__":  # CI runs it on a bare checkout: stdlib only, no install
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=ALLOWLIST.parent)
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    parser.add_argument("--summary", action="store_true")
    ns = parser.parse_args()
    raise SystemExit(main(ns.root, ns.allowlist, ns.summary))
