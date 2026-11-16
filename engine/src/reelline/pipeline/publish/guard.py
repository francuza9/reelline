"""A reel's texts and files, and the guard that runs before anything is uploaded."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import PublishError
from reelline.media.share import version_for
from reelline.paths import stored_path
from reelline.pipeline.publish.base import enabled_channels, label


def reel_row(conn: sqlite3.Connection, reel_id: int) -> sqlite3.Row:
    reel = conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()
    if reel is None:
        raise PublishError(f"No reel #{reel_id}")
    return reel


def texts(conn: sqlite3.Connection, reel: sqlite3.Row) -> dict:
    """{"caption": the assembled post caption, "youtube_title", "source_line", "cover_offset_ms"}."""
    cap = conn.execute(
        "SELECT youtube_title FROM captions WHERE script_id = ? ORDER BY id DESC LIMIT 1", (reel["script_id"],)
    ).fetchone()
    prefix = conf().steps.caption.source_line.split("{", 1)[0]
    caption = reel["caption"] or ""
    source = next((line for line in caption.splitlines() if line.startswith(prefix)), None)
    return {
        "caption": caption,
        "youtube_title": cap["youtube_title"] if cap else None,
        "source_line": source,
        "cover_offset_ms": cover_offset_ms(reel),
    }


def cover_offset_ms(reel: sqlite3.Row) -> int:
    """Instagram's and TikTok's cover frame (Buffer's thumbnailOffset): publish.thumbnail_offset_ms
    into the reel itself, so moved past a cold open (reels.gag_s, reelline/media/gags.py): never a gag frame."""
    gag = reel["gag_s"] if "gag_s" in reel.keys() else None  # noqa: SIM118  (sqlite3.Row: `in` tests values)
    return conf().publish.thumbnail_offset_ms + round((gag or 0.0) * 1000)


def shown_images(reel: sqlite3.Row) -> list[str]:
    """The images the reel shows (its images.json); CreditsError when it's recorded but
    missing or unreadable, so a lost file never reads as "no images"."""
    from reelline.pipeline.credits import CreditsError
    from reelline.pipeline.credits import shown_images as shown

    if not reel["images_path"]:
        return []
    path = stored_path(reel["images_path"])
    if not path.is_file():
        raise CreditsError(f"{reel['images_path']} is missing: can't tell which images the reel shows")
    return shown(path)


def channel_files(reel: sqlite3.Row, channels: list[str]) -> dict[str, Path]:
    """Each channel's file: its platform version next to the reel's reel.mp4
    (reelline/media/share.py version_for(); reel.mp4 itself for a reel from before the versions)."""
    mp4 = stored_path(reel["mp4_path"])
    return {ch: version_for(mp4, ch) for ch in channels}


def file_problems(mp4: Path, probe: Callable[[Path], dict]) -> list[str]:
    """What stops one file from going out: its size, and its duration, size, codec and
    frame rate as PUBLISH allows (probed again)."""
    problems = []
    mb = mp4.stat().st_size / 1e6
    if mb > conf().publish.max_mb:
        problems.append(f"{mb:.0f} MB, over {conf().publish.max_mb} MB")
    try:
        info = probe(mp4)
    except Exception as exc:  # ffprobe missing or failing
        return problems + [f"can't probe {mp4.name}: {exc}"]
    lo, hi = conf().publish.duration_s
    if not lo <= info["duration"] <= hi:
        problems.append(f"duration {info['duration']:.1f}s outside {lo:g}-{hi:g}s")
    v = info.get("video")
    if v is None:
        problems.append("no video stream")
    else:
        if (v["width"], v["height"]) != tuple(conf().publish.size_px):
            problems.append(f"{v['width']}x{v['height']}, not {conf().publish.size_px[0]}x{conf().publish.size_px[1]}")
        if v["codec"] != conf().publish.video_codec:
            problems.append(f"codec {v['codec']}, not {conf().publish.video_codec}")
        if not conf().publish.fps[0] <= v["fps"] <= conf().publish.fps[1]:
            problems.append(f"{v['fps']:g} fps outside {conf().publish.fps[0]}-{conf().publish.fps[1]}")
    if not info.get("audio"):
        problems.append("no audio stream")
    return problems


def guard(
    conn: sqlite3.Connection,
    reel: sqlite3.Row,
    channels: list[str] | None = None,
    probe: Callable[[Path], dict] | None = None,
) -> list[str]:
    """What stops this reel from going out (empty: fine). Each channel's file
    (channel_files()) is probed again: its duration, size, codec and frame rate as PUBLISH
    allows. The caption must keep the credits rule (reelline/pipeline/credits.py caption_problems())."""
    if probe is None:
        from reelline.pipeline.qa import probe
    channels = channels or enabled_channels()
    mp4 = stored_path(reel["mp4_path"]) if reel["mp4_path"] else None
    if mp4 is None or not mp4.is_file():
        return [f"no reel file ({reel['mp4_path'] or 'no path'})"]
    problems = []
    by_file: dict[Path, list[str]] = {}
    for ch, f in channel_files(reel, channels).items():
        by_file.setdefault(f, []).append(ch)
    for f, chs in by_file.items():
        found = file_problems(f, probe)
        whose = "" if len(by_file) == 1 else f"{f.name} ({', '.join(label(c) for c in chs)}): "
        problems += [whose + x for x in found]
    t = texts(conn, reel)
    if not t["caption"].strip():
        problems.append("no caption")
    from reelline.pipeline.credits import CreditsError, caption_problems
    from reelline.pipeline.shortlinks import caption_problems as link_problems

    try:
        problems += caption_problems(t["caption"], reel["id"], shown_images(reel))
    except CreditsError as exc:
        problems.append(f"credits: {exc}")
    problems += link_problems(conn, t["caption"], reel["id"])
    if "youtube" in channels and not (t["youtube_title"] or "").strip():
        problems.append("no YouTube title")
    if "facebook" in channels:
        cover = stored_path(reel["cover_path"]) if reel["cover_path"] else None
        if cover is None or not cover.is_file():
            problems.append("no cover.png (Facebook's thumbnail)")
    return problems
