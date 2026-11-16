"""The share arrow and the reel's four platform versions.

While the host says the share line (@share, channel docs/scripts.md "The share line") an animated arrow
points at the platform's own share button (remotion/src/ShareArrow.tsx draws it). The
button sits in a different place on Instagram, TikTok, YouTube and Facebook, so each gets
its own version of the reel: reel_<platform>.mp4 in the reel's dir, reel.mp4 being the
review's (share.json reviewPlatform, Instagram). The numbers live in
remotion/src/share.json, shared with Remotion; this module loads and checks them, times the
arrow from the script and joins the versions.

The versions differ only while the arrow is on, so scripts/render_reel.py renders the
reel's frames once without it and only the arrow's stretch once per platform
(segments()): the head [0, a), each platform's [a, b), the rest [b, end), every one muted
from one Remotion bundle. join() puts each platform's three video segments back together
by stream copy (each segment is a whole H.264 stream, so every join is on a keyframe and
nothing is encoded again) and lays the one audio track under them: the mix is encoded to
AAC once (encode_audio()) for all four, so there is no audio join, no gap and no click.
check_join() verifies each file: the frames add up, the audio is that one track bit for bit.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reelline.core.errors import ReellineError
from reelline.media.script_markup import Script
from reelline.paths import REMOTION_DIR

SHARE_JSON = REMOTION_DIR / "src" / "share.json"
# The platforms, in reelline/pipeline/publish.py CHANNELS order (the channel names).
PLATFORMS = ("instagram", "tiktok", "youtube", "facebook")
FILE = "reel_{platform}.mp4"
# The review's file (scripts/render_reel.py writes it; the review and QA read it).
REEL_FILE = "reel.mp4"
# The one audio track all four versions carry: AAC as Remotion encodes it (its default).
AUDIO_CODEC = ["-c:a", "aac", "-b:a", "320k"]


class ShareError(ReellineError, ValueError):
    """A bad share.json, or a platform version that can't be made."""


@dataclass(frozen=True)
class ShareConfig:
    on: bool
    appear_delay_s: float
    draw_s: float
    exit_s: float
    review_platform: str
    platforms: dict[str, dict]


def load(path: Path = SHARE_JSON) -> ShareConfig:
    """share.json, checked: the four platforms, each with its button inside the frame."""
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
        cfg = ShareConfig(
            bool(spec["on"]),
            float(spec["appearDelayS"]),
            float(spec["drawS"]),
            float(spec["exitS"]),
            str(spec["reviewPlatform"]),
            dict(spec["platforms"]),
        )
        arrow, label = spec["arrow"], spec["label"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ShareError(f"Can't read {path}: {exc}") from exc
    if set(cfg.platforms) != set(PLATFORMS):
        raise ShareError(
            f"{path.name}: platforms must be exactly {', '.join(PLATFORMS)}, got {', '.join(cfg.platforms)}"
        )
    if cfg.review_platform not in PLATFORMS:
        raise ShareError(
            f"{path.name}: reviewPlatform must be one of {', '.join(PLATFORMS)}, got {cfg.review_platform!r}"
        )
    if not (0 <= cfg.appear_delay_s <= 2 and 0 < cfg.draw_s <= 2 and 0 < cfg.exit_s <= 1):
        raise ShareError(
            f"{path.name}: appearDelayS in [0, 2], drawS in (0, 2] and exitS in (0, 1] s, got "
            f"{cfg.appear_delay_s}, {cfg.draw_s}, {cfg.exit_s}"
        )
    for name, p in cfg.platforms.items():
        try:
            x, y = (float(v) for v in p["button"])
            angle, gap = float(p["angleDeg"]), float(p["gapPx"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ShareError(f"{path.name}: platform {name} needs button [x, y], angleDeg and gapPx ({exc})") from None
        if not (0 <= x <= 1080 and 0 <= y <= 1920) or gap < 0 or not -360 <= angle <= 360:
            raise ShareError(
                f"{path.name}: platform {name}: button {p['button']} must be inside 1080x1920, "
                f"gapPx >= 0, angleDeg in [-360, 360]"
            )
    if arrow["strokePx"] <= 0 or arrow["lengthPx"] <= 0 or not label["text"].strip():
        raise ShareError(f"{path.name}: arrow strokePx and lengthPx must be > 0 and the label's text not empty")
    if "—" in label["text"]:
        raise ShareError(f"{path.name}: use hyphens (-), never em dashes, in the label")
    return cfg


def share_span(script: Script, words: list[dict[str, Any]]) -> tuple[float, float] | None:
    """(the share line's first word's start, its last word's end) in seconds; None for a
    script without one. `words` are the timed display tokens (time_tokens()), one per
    script.tokens entry."""
    n = next((i for i, p in enumerate(script.paragraphs) if p.share), None)
    if n is None:
        return None
    timed = [w for t, w in zip(script.tokens, words, strict=False) if t.paragraph == n and w["start"] is not None]
    if not timed:
        return None
    return timed[0]["start"], max(w["end"] for w in timed)


def share_props(span: tuple[float, float] | None, cfg: ShareConfig, platform: str | None) -> dict | None:
    """Remotion's `share` prop (remotion/src/types.ts ShareProps): the line, and the arrow
    on from appearDelayS after its first word until exitS after its last; `platform` None:
    the same timing without an arrow. None without a share line or with the arrow off."""
    if span is None or not cfg.on:
        return None
    if platform is not None and platform not in PLATFORMS:
        raise ShareError(f"Unknown platform {platform!r} (have: {', '.join(PLATFORMS)})")
    start, end = span
    return {
        "lineStart": round(start, 3),
        "lineEnd": round(end, 3),
        "start": round(start + cfg.appear_delay_s, 3),
        "end": round(end + cfg.exit_s, 3),
        "platform": platform,
    }


@dataclass(frozen=True)
class Segments:
    """Frame ranges (Remotion --frames): the head before the arrow, the arrow's stretch (one
    render per platform) and the rest after it, None when the arrow runs to the end."""

    head: str
    window: str
    rest: str | None
    first: int  # the arrow's stretch: frames [first, stop)
    stop: int | None

    def describe(self, fps: int) -> str:
        stop = f"{self.stop / fps:.2f}s" if self.stop is not None else "the end"
        return f"frames {self.first}-{'' if self.stop is None else self.stop - 1} ({self.first / fps:.2f}s-{stop})"


def segments(share: dict, frames: int, fps: int, offset: int = 0) -> Segments:
    """The three ranges around the arrow (`share`: share_props()): ShareArrow.tsx draws it
    while start <= frame / fps < end, so frames [floor(start * fps), ceil(end * fps) + 1)
    hold every frame it is on. `frames` is the video's length in frames; when the stretch
    reaches its last frame the window runs to the end and there is no rest. `offset`: the
    frames before the reel in the video (a cold open, reelline/media/gags.py), which `share`'s times
    don't count."""
    first = offset + math.floor(share["start"] * fps)
    stop = offset + math.ceil(share["end"] * fps) + 1
    if not 0 < first < frames:
        raise ShareError(f"The share arrow starts at frame {first}, outside the reel's 1-{frames - 1}")
    if stop >= frames - 1:
        return Segments(f"0-{first - 1}", f"{first}-", None, first, None)
    return Segments(f"0-{first - 1}", f"{first}-{stop - 1}", f"{stop}-", first, stop)


def reel_frames(seconds: float, fps: int) -> int:
    """The reel's length in frames as remotion/src/Root.tsx calculateMetadata counts it."""
    return math.ceil(seconds * fps - 1e-9)


# ---- The files ----------------------------------------------------------------------------


def platform_file(work: Path, platform: str) -> Path:
    return work / FILE.format(platform=platform)


def version_for(mp4: Path, platform: str) -> Path:
    """The file to publish on `platform` for a reel whose review file is `mp4`
    (<reel dir>/reel.mp4): its reel_<platform>.mp4, else reel.mp4 itself (a reel rendered
    before the versions)."""
    own = platform_file(mp4.parent, platform)
    return own if own.is_file() else mp4


def clear_versions(work: Path) -> None:
    """Remove the platform files of an earlier render (a render without a share line
    must not leave the old arrows to publish)."""
    for platform in PLATFORMS:
        platform_file(work, platform).unlink(missing_ok=True)


def link(src: Path, dest: Path) -> None:
    """`dest` the same file as `src`: a hard link (a copy where links can't be made)."""
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def link_versions(work: Path, mp4: Path) -> dict[str, Path]:
    """Every platform's file the same as `mp4` (no arrow to differ by)."""
    out = {}
    for platform in PLATFORMS:
        link(mp4, platform_file(work, platform))
        out[platform] = platform_file(work, platform)
    return out


# ---- ffmpeg --------------------------------------------------------------------------------


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ShareError(f"{what} failed: {r.stderr.strip()[-600:]}")
    return r


def encode_audio(wav: Path, out: Path) -> Path:
    """The reel's audio (the mix, else the voice) as one AAC track for all four versions."""
    _run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(wav), "-vn", *AUDIO_CODEC, str(out)],
        f"Encoding {wav.name} to AAC",
    )
    return out


def video_header(mp4: Path) -> str:
    """A hash of the file's H.264 parameter sets (the stream's extradata): segments joined
    by stream copy must share them."""
    r = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-show_data_hash",
            "sha256",
            "-of",
            "json",
            str(mp4),
        ],
        f"Probing {mp4.name}",
    )
    streams = json.loads(r.stdout).get("streams") or []
    if not streams:
        raise ShareError(f"{mp4.name} has no video stream")
    s = streams[0]
    return f"{s.get('codec_name')}:{s.get('width')}x{s.get('height')}:{s.get('pix_fmt')}:{s.get('extradata_hash')}"


def packet_count(mp4: Path, stream: str = "v:0") -> int:
    r = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            stream,
            "-count_packets",
            "-show_entries",
            "stream=nb_read_packets",
            "-of",
            "csv=p=0",
            str(mp4),
        ],
        f"Counting {mp4.name}'s packets",
    )
    return int(r.stdout.strip().split(",")[0] or 0)


def stream_hash(path: Path, stream: str) -> str:
    """MD5 of a stream's packets as they are (no decoding)."""
    r = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            f"0:{stream}",
            "-c",
            "copy",
            "-f",
            "md5",
            "-",
        ],
        f"Hashing {path.name}'s {stream}",
    )
    return r.stdout.strip()


def join(parts: list[Path], audio: Path, out: Path, tmp: Path) -> Path:
    """`out`: the video segments `parts` back to back (stream copy) with `audio` under them,
    faststart. Raises ShareError when the segments' H.264 headers differ (they can't be
    joined without encoding again)."""
    headers = {p.name: video_header(p) for p in parts}
    if len(set(headers.values())) != 1:
        raise ShareError(
            "The segments' video streams differ, so they can't be joined by stream copy: "
            + "; ".join(f"{k} {v}" for k, v in headers.items())
        )
    listing = tmp / f"{out.stem}.ffconcat"
    listing.write_text("ffconcat version 1.0\n" + "".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    part = out.with_name(out.stem + ".part.mp4")
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(part),
        ],
        f"Joining {out.name}",
    )
    part.replace(out)
    return out


def check_join(out: Path, parts: list[Path], audio: Path) -> dict:
    """What the joined file must be: every segment's frames (none lost or doubled at a
    join) and the audio track bit for bit (one encode, no join in it). Returns
    {"frames", "audio_md5"}; ShareError otherwise."""
    frames = packet_count(out)
    want = sum(packet_count(p) for p in parts)
    if frames != want:
        raise ShareError(f"{out.name} has {frames} video frames, its segments {want}")
    got, expected = stream_hash(out, "a:0"), stream_hash(audio, "a:0")
    if got != expected:
        raise ShareError(f"{out.name}'s audio isn't the reel's one track ({got} vs {expected})")
    return {"frames": frames, "audio_md5": got.removeprefix("MD5=")}
