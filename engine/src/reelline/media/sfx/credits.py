"""SFX credit lines in out/reels/<name>/attributions.txt.

The file starts with the image lines (reelline/media/images/resolve.py write_outputs); the
SFX get a section of their own after them: CREDITS["header"], then one line per
distinct sound the reel uses, in order of first use. Neither license requires the
credit; caption output leaves the section out unless CREDITS["in_captions"] is on.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from reelline.media.sfx.config import CREDITS
from reelline.media.sfx.library import Sound


def site_name(source_url: str) -> str:
    host = (urlparse(source_url).hostname or "").lower()
    for domain, name in CREDITS["sites"].items():
        if host == domain or host.endswith(f".{domain}"):
            return name
    return host.removeprefix("www.") or "unknown"


def credit_line(sound: Sound) -> str:
    author = sound.author.strip()
    limit = CREDITS["author_max_chars"]
    if len(author) > limit:
        author = author[: limit - 1].rstrip() + "…"
    return CREDITS["line"].format(author=author or "unknown", site=site_name(sound.source_url))


def credit_lines(sounds: list[Sound]) -> list[str]:
    """One line per distinct sound, in order of first use (two sounds with the same
    credit give one line)."""
    out: list[str] = []
    for line in (credit_line(s) for s in sounds):
        if line not in out:
            out.append(line)
    return out


def split_attributions(text: str) -> tuple[list[str], list[str]]:
    """(image lines, SFX lines) of an attributions.txt."""
    lines = [line for line in text.splitlines() if line.strip()]
    if CREDITS["header"] not in lines:
        return lines, []
    at = lines.index(CREDITS["header"])
    return lines[:at], lines[at + 1 :]


def write_credits(path: Path, sounds: list[Sound]) -> list[str]:
    """Rewrite the SFX section of `path` for `sounds` (none: no section), keeping the
    image lines, with a blank line between the two. A missing file is only created
    when there's something to write. Returns the SFX lines."""
    images, _ = split_attributions(path.read_text(encoding="utf-8")) if path.is_file() else ([], [])
    sfx = credit_lines(sounds)
    lines = images + (([""] if images else []) + [CREDITS["header"], *sfx] if sfx else [])
    if lines or path.is_file():
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return sfx
