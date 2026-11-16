"""Send a rendered reel to Telegram for review on the phone.

Usage: python scripts/send_to_phone.py <reel-name | reel dir | path/to/reel.mp4> [--caption "..."]

A reel name or dir resolves to out/reels/<name>/reel.mp4. The video goes out with
sendVideo (multipart) with supports_streaming and the file's width, height and
duration (ffprobe), so Telegram plays it as a vertical video.

Default caption: reel name, duration, background, mood (from reel_props.json),
plus, when the files exist, the aligner's "check these" words (check_these.json)
and the wall-TV images (images.json: paragraph, source, license); the SFX credits
from attributions.txt only with reelline/media/sfx/config.py CREDITS["in_captions"] on (off
by default). --caption replaces the header line; the lists still follow. Whatever
doesn't fit Telegram's caption limit goes out as follow-up text message(s).
send_reel(..., check_these=False) leaves the check-these list out (reelline/pipeline/produce.py
shows it once, after QA's re-roll, in its status block).
out/reels/<name>/cover.png is sent too when it exists.

A reel over the bot's upload limit (a 12 Mbps reel.mp4 of a minute is ~80 MB) goes out
as <stem>_preview.mp4 next to it (reel_preview.mp4): the same frames re-encoded with
ffmpeg (H.264, yuv420p, same size, audio copied, faststart) at a video bitrate from the
duration so the file lands near PREVIEW["target_bytes"], at most
PREVIEW["max_video_kbps"]; kept and reused until reel.mp4 is newer. reel.mp4 itself is
what publishing and QA use, untouched. A reel within the limit goes out as it is.

Credentials: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env (reelline/config/credentials.py).
The token is never printed; error messages have it redacted.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from reelline.config.credentials import ConfigError
from reelline.media.sfx.config import CREDITS as SFX_CREDITS
from reelline.media.sfx.credits import split_attributions
from reelline.notify import NotifyError, get_notifier
from reelline.paths import REPO_ROOT

REELS_DIR = REPO_ROOT / "out" / "reels"
REEL_FILE = "reel.mp4"
COVER_FILE = "cover.png"

# The review chat's upload limits (Telegram Bot API, "Sending files" and per-method docs): the
# preview and the caption split are made to fit them. Sizes in bytes; MB taken as 10^6 to stay on
# the safe side. The sending itself is the Notifier's (notify.channel; reelline/notify/telegram.py
# has the upload timeouts).
TELEGRAM = {
    "max_video_bytes": 50 * 1000 * 1000,  # multipart upload, non-photo files
    "max_photo_bytes": 10 * 1000 * 1000,
    "caption_max_chars": 1024,
    "text_max_chars": 4096,
}

# The review copy of a reel over max_video_bytes (upload_file()).
PREVIEW = {
    "suffix": "_preview",  # reel.mp4 -> reel_preview.mp4
    "target_bytes": 45 * 1000 * 1000,  # the size aimed at, under the 50 MB limit
    "max_video_kbps": 5500,
    "min_video_kbps": 500,
    "container_share": 0.02,  # of the file, kept free for the MP4's own overhead
    "default_audio_kbps": 320,  # when ffprobe gives no audio bitrate
    "preset": "fast",
}


class SendError(RuntimeError):
    """Raised when the reel can't be found or is too big (a failed send: reelline.notify.NotifyError)."""


def resolve_reel(target: str | Path) -> Path:
    """reel name | reel dir | path to an .mp4 -> the .mp4 path (must exist)."""
    path = Path(target)
    if path.suffix == ".mp4":
        mp4 = path
    elif path.is_dir():
        mp4 = path / REEL_FILE
    elif len(path.parts) == 1:
        mp4 = REELS_DIR / path.name / REEL_FILE
    else:
        raise SendError(f"{target}: not a reel name, a reel dir or an .mp4 path")
    if not mp4.is_file():
        raise SendError(f"Reel video not found: {mp4}")
    return mp4.resolve()


def probe_video(mp4: Path) -> dict:
    """{width, height, duration} of the first video stream (duration from the container)."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(mp4),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SendError(f"ffprobe failed on {mp4}: {result.stderr.strip()}")
    info = json.loads(result.stdout)
    if not info.get("streams"):
        raise SendError(f"{mp4} has no video stream")
    stream = info["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": float(info["format"]["duration"]),
    }


def probe_audio_kbps(mp4: Path) -> float | None:
    """The first audio stream's bitrate in kbps; None without audio."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=bit_rate",
            "-of",
            "json",
            str(mp4),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SendError(f"ffprobe failed on {mp4}: {result.stderr.strip()}")
    streams = json.loads(result.stdout).get("streams") or []
    if not streams:
        return None
    rate = streams[0].get("bit_rate")
    return int(rate) / 1000 if rate and str(rate).isdigit() else PREVIEW["default_audio_kbps"]


def preview_video_kbps(duration_s: float, audio_kbps: float | None) -> int:
    """The preview's video bitrate: PREVIEW["target_bytes"] over the duration, less the
    container's share and the (copied) audio, within min/max_video_kbps."""
    budget = PREVIEW["target_bytes"] * 8 / 1000 * (1 - PREVIEW["container_share"]) / max(duration_s, 1.0)
    kbps = budget - (audio_kbps or 0)
    return int(max(PREVIEW["min_video_kbps"], min(PREVIEW["max_video_kbps"], kbps)))


def preview_path(mp4: Path) -> Path:
    return mp4.with_name(f"{mp4.stem}{PREVIEW['suffix']}{mp4.suffix}")


def build_preview(mp4: Path, preview: Path, duration_s: float) -> None:
    """mp4 re-encoded into preview (written to a .part file, then moved into place)."""
    kbps = preview_video_kbps(duration_s, probe_audio_kbps(mp4))
    part = preview.with_name(f"{preview.stem}.part{preview.suffix}")
    print(f"{mp4.name} is over the upload limit: building {preview.name} at {kbps} kbps video...", flush=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(mp4),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            PREVIEW["preset"],
            "-b:v",
            f"{kbps}k",
            "-maxrate",
            f"{kbps * 3 // 2}k",
            "-bufsize",
            f"{kbps * 2}k",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(part),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        part.unlink(missing_ok=True)
        raise SendError(f"ffmpeg preview of {mp4} failed: {result.stderr.strip()[-500:]}")
    os.replace(part, preview)


def upload_file(mp4: Path, duration_s: float) -> Path:
    """The file to upload: mp4 itself when it's within the bot's limit, else its
    preview, built when missing or older than mp4."""
    if mp4.stat().st_size <= TELEGRAM["max_video_bytes"]:
        return mp4
    preview = preview_path(mp4)
    if not preview.is_file() or preview.stat().st_mtime < mp4.stat().st_mtime:
        build_preview(mp4, preview, duration_s)
    size = preview.stat().st_size
    if size > TELEGRAM["max_video_bytes"]:
        raise SendError(
            f"{preview} is {size / 1e6:.1f} MB; the bot upload limit is {TELEGRAM['max_video_bytes'] / 1e6:.0f} MB"
        )
    return preview


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SendError(f"Can't read {path}: {exc}") from exc


def caption_lines(
    reel_dir: Path, name: str, duration: float, header: str | None, check_these: bool = True
) -> list[str]:
    """The caption as lines: header, then the check-these (unless `check_these` is off)
    and image lists when present."""
    if header is None:
        parts = [name, f"{duration:.1f}s"]
        props_path = reel_dir / "reel_props.json"
        if props_path.is_file():
            props = _read_json(props_path)
            parts += [f"bg {Path(props['background']).stem}", f"mood {props['mood']}"]
        else:
            print(f"NOTE: {props_path} not found; caption has no background/mood")
        header = " | ".join(parts)
    lines = [header]

    check_path = reel_dir / "check_these.json"
    if check_these and check_path.is_file():
        rows = _read_json(check_path)
        lines.append(f"Check these ({len(rows)}):" if rows else "Check these: none")
        lines += [f"  ¶{r['paragraph']} {r['start']:.1f}s {r['word']} ({r['score']:.2f})" for r in rows]

    images_path = reel_dir / "images.json"
    if images_path.is_file():
        manifest = _read_json(images_path)
        lines.append("Images:")
        for p in manifest["paragraphs"]:
            if p["status"] == "image":
                lines.append(f"  ¶{p['paragraph']} {p['source']}, {p['license']}")
            elif p["status"] == "no_candidate":
                lines.append(f"  ¶{p['paragraph']} no image ({p['value']})")

    # SFX credits only when reelline/media/sfx/config.py CREDITS["in_captions"] is on (off by default).
    credits_path = reel_dir / "attributions.txt"
    if SFX_CREDITS["in_captions"] and credits_path.is_file():
        _, sfx = split_attributions(credits_path.read_text(encoding="utf-8"))
        if sfx:
            lines += ["SFX:", *(f"  {line}" for line in sfx)]
    return lines


def split_caption(lines: list[str], caption_max: int, text_max: int) -> tuple[str, list[str]]:
    """Whole lines into a caption of at most caption_max chars; the rest into
    follow-up messages of at most text_max chars each. A single line longer than
    the limit is cut at the limit."""

    def pack(rest: list[str], limit: int) -> tuple[str, list[str]]:
        out: list[str] = []
        while rest:
            candidate = "\n".join([*out, rest[0]])
            if len(candidate) > limit:
                if not out:  # one over-long line: cut it
                    return rest[0][:limit], [rest[0][limit:], *rest[1:]]
                break
            out.append(rest.pop(0))
        return "\n".join(out), rest

    caption, rest = pack(list(lines), caption_max)
    messages = []
    while rest:
        text, rest = pack(rest, text_max)
        messages.append(text)
    return caption, messages


def send_reel(target: str | Path, caption: str | None = None, check_these: bool = True, notifier=None) -> None:
    """Send the reel (plus overflow text and the cover, if any) to the review chat (the Notifier:
    notify.channel's, or the one handed in, e.g. the review channel)."""
    mp4 = resolve_reel(target)
    reel_dir = mp4.parent
    name = reel_dir.name if mp4.name == REEL_FILE else mp4.stem
    video = probe_video(mp4)
    text, overflow = split_caption(
        caption_lines(reel_dir, name, video["duration"], caption, check_these),
        TELEGRAM["caption_max_chars"],
        TELEGRAM["text_max_chars"],
    )
    cover = reel_dir / COVER_FILE
    if cover.is_file() and cover.stat().st_size > TELEGRAM["max_photo_bytes"]:
        raise SendError(
            f"{cover} is {cover.stat().st_size / 1e6:.1f} MB; the photo limit is "
            f"{TELEGRAM['max_photo_bytes'] / 1e6:.0f} MB"
        )

    notifier = get_notifier(notifier)
    upload = upload_file(mp4, video["duration"])
    size = upload.stat().st_size
    print(
        f"Sending {upload} ({size / 1e6:.1f} MB, {video['width']}x{video['height']}, "
        f"{video['duration']:.1f}s) to {notifier.name.capitalize()}...",
        flush=True,
    )
    notifier.send_file(
        f"{name}.mp4",
        upload,
        kind="video",
        caption=text,
        mime="video/mp4",
        meta={
            "supports_streaming": "true",
            "width": str(video["width"]),
            "height": str(video["height"]),
            "duration": str(round(video["duration"])),
        },
    )
    if overflow:
        print(
            f"Caption over {TELEGRAM['caption_max_chars']} chars: "
            f"sending the rest as {len(overflow)} follow-up message(s)"
        )
        for message in overflow:
            notifier.send(message)
    if cover.is_file():
        notifier.send_file(COVER_FILE, cover, kind="photo", caption=f"{name} cover", mime="image/png")
        print(f"Sent cover: {cover}")
    print("Sent.")


def send_photo(path: Path, caption: str, notifier=None) -> None:
    """One PNG/JPEG to the review chat (render_reel.py --background-check)."""
    if not path.is_file():
        raise SendError(f"Photo not found: {path}")
    if path.stat().st_size > TELEGRAM["max_photo_bytes"]:
        raise SendError(
            f"{path} is {path.stat().st_size / 1e6:.1f} MB; the photo limit is "
            f"{TELEGRAM['max_photo_bytes'] / 1e6:.0f} MB"
        )
    get_notifier(notifier).send_file(
        path.name, path, kind="photo", caption=caption[: TELEGRAM["caption_max_chars"]], mime="image/png"
    )
    print(f"Sent photo: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reel", help="reel name, reel dir (out/reels/<name>) or path to an .mp4")
    parser.add_argument("--caption", default=None, help="caption header (default: name, duration, background, mood)")
    args = parser.parse_args()
    try:
        send_reel(args.reel, args.caption)
    except (SendError, ConfigError, NotifyError) as exc:
        sys.exit(f"FAIL: {exc}")


if __name__ == "__main__":
    main()
