"""Pinned tag audio: one take of TAG_LINE that every reel reuses, so the channel's most
repeated line sounds identical in every reel.

Usage:
  python scripts/tag_audio.py             TAG_TAKES fresh takes -> out/tag_takes/ + Telegram
  python scripts/tag_audio.py --pick N    take N -> voice/tag.wav

Takes: TTS of TAG_LINE (reelline/media/script_markup.py) with the channel's voice and the base style
(tts_style(None), no @style), each cleaned like any reel clip: edge trim and loudnorm
(reelline/media/tts.py clean_clip, no tempo; the reel applies TTS_TEMPO). They're saved as
out/tag_takes/take_N.wav, with takes.json recording the line, model and style, and
sent to the review chat as numbered audio messages (MP3 for Telegram's player) with
the Notifier (notify.channel). Its settings are checked before any TTS call.

--pick N copies take N to voice/tag.wav, replacing an earlier pick. It fails if the
takes were made for a different TAG_LINE.

render_reel.py uses voice/tag.wav for the tag paragraph of every reel instead of TTS
(load_pinned). It goes through the same tempo, alignment and lip sync as any clip;
its alignment is cached in out/tag_align.json (tag_letters), keyed by the tempo'd
clip, the line and the aligner's settings, and shifted into place in the reel's
letters (merge_letters). Without voice/tag.wav the tag paragraph uses TTS as usual,
and the render says so.

Credentials: GEMINI_API_KEY, the voice id variable, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
from .env (reelline/config/credentials.py); none are printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from reelline.config import conf
from reelline.paths import REPO_ROOT, SCRIPTS_DIR

sys.path.insert(0, str(SCRIPTS_DIR))

import align as aligner
from reelline.config.credentials import ConfigError, load_settings
from reelline.media.script_markup import PARAGRAPH_SEP, TAG_LINE
from reelline.media.tts import TTSError, call_row, clean_clip, cost_usd, synthesize, tts_model, tts_style
from reelline.notify import NotifyError, get_notifier
from reelline.pipeline.db import manual_recording, record_manual
from reelline.providers import connection
from send_to_phone import SendError

TAG_AUDIO = conf().channel.dir / "voice" / "tag.wav"
TAKES_DIR = REPO_ROOT / "out" / "tag_takes"
TAKES_MANIFEST = "takes.json"
ALIGN_CACHE = REPO_ROOT / "out" / "tag_align.json"
# How many takes one run makes (one TTS call each).
TAG_TAKES = 5
# Telegram's sendAudio plays MP3 inline (not WAV); takes are encoded only for sending.
SEND_MP3_BITRATE = "192k"


class TagAudioError(RuntimeError):
    """Raised for a missing or bad take, a bad pinned clip, or a letters mismatch."""


def take_path(n: int) -> Path:
    return TAKES_DIR / f"take_{n}.wav"


# --- takes -------------------------------------------------------------------------------


def generate_takes(client, voice: str, count: int = TAG_TAKES) -> tuple[list[Path], float]:
    """`count` fresh TTS takes of TAG_LINE, cleaned, saved as take_1..take_N.wav (older
    takes are removed first). Returns (paths, cost in USD). The takes' cost is recorded
    in llm_calls (tts_tag, manual), the ones made before a failure too."""
    if TAKES_DIR.exists():
        shutil.rmtree(TAKES_DIR)
    TAKES_DIR.mkdir(parents=True)
    style = tts_style(None)
    paths, cost, calls = [], 0.0, []
    try:
        with manual_recording():  # each take's cost written as it's made
            for n in range(1, count + 1):
                print(f"TTS take {n}/{count}...", flush=True)
                speech = synthesize(client, TAG_LINE, voice, style)
                calls.append(call_row(speech.input_tokens, speech.output_tokens, "tts_tag"))
                cost += cost_usd(speech.input_tokens, speech.output_tokens)
                path = take_path(n)
                path.write_bytes(clean_clip(speech.wav))
                paths.append(path)
    finally:
        record_manual(calls)
    manifest = {"line": TAG_LINE, "model": tts_model(), "style": style, "takes": [p.name for p in paths]}
    (TAKES_DIR / TAKES_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return paths, cost


def to_mp3(wav: Path, mp3: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise TagAudioError("ffmpeg not found on PATH; needed to encode takes for Telegram")
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav),
            "-c:a",
            "libmp3lame",
            "-b:a",
            SEND_MP3_BITRATE,
            str(mp3),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TagAudioError(f"ffmpeg MP3 encode of {wav.name} failed:\n{result.stderr}")


def send_takes(notifier, paths: list[Path]) -> None:
    """Each take as a numbered audio message, then how to pick one."""
    with tempfile.TemporaryDirectory() as tmp:
        for n, wav in enumerate(paths, start=1):
            mp3 = Path(tmp) / f"tag_take_{n}.mp3"
            to_mp3(wav, mp3)
            notifier.send_file(
                mp3.name,
                mp3,
                kind="audio",
                caption=f"Tag take {n}/{len(paths)}: {TAG_LINE}",
                mime="audio/mpeg",
                meta={"title": f"Tag take {n}", "performer": conf().channel.shown_name()},
            )
            print(f"Sent take {n}: {wav}")
    notifier.send("Pick one: python scripts/tag_audio.py --pick N")


def pick(n: int) -> Path:
    """Copy take `n` to TAG_AUDIO; fails for a missing take or takes of another line."""
    manifest_path = TAKES_DIR / TAKES_MANIFEST
    if not manifest_path.is_file():
        raise TagAudioError(f"No takes: {manifest_path} not found; run scripts/tag_audio.py first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["line"] != TAG_LINE:
        raise TagAudioError(
            f"The takes in {TAKES_DIR} say {manifest['line']!r}, but TAG_LINE is {TAG_LINE!r}; make new takes first"
        )
    count = len(manifest["takes"])
    if not 1 <= n <= count:
        raise TagAudioError(f"--pick {n}: there are takes 1..{count}")
    src = take_path(n)
    if not src.is_file():
        raise TagAudioError(f"Take {n} is missing: {src}")
    replaced = TAG_AUDIO.is_file()
    TAG_AUDIO.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, TAG_AUDIO)
    print(f"Pinned take {n} as {TAG_AUDIO}" + (" (replaced the earlier pick)" if replaced else ""))
    return TAG_AUDIO


# --- the pinned clip in a reel --------------------------------------------------------


def load_pinned() -> bytes | None:
    """TAG_AUDIO's bytes, or None when there's no pinned tag. Fails for a non-WAV file."""
    if not TAG_AUDIO.is_file():
        return None
    wav = TAG_AUDIO.read_bytes()
    if not wav.startswith(b"RIFF"):
        raise TagAudioError(f"{TAG_AUDIO} isn't a WAV file (first bytes {wav[:4]!r}); pick a take again")
    return wav


def _align_key(clip: bytes) -> str:
    parts = [TAG_LINE, hashlib.sha256(clip).hexdigest(), aligner.ENERGY_WINDOW_S, aligner.QUIET_DB]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def tag_letters(clip: bytes, align: Callable[[Path, str], list[dict]] | None = None) -> list[dict]:
    """Letters of TAG_LINE aligned to `clip` (the tag clip as it goes into the reel:
    cleaned and tempo'd), from ALIGN_CACHE when its key matches, else aligned and
    cached. Indices are into TAG_LINE, times from the clip's start."""
    key = _align_key(clip)
    if ALIGN_CACHE.is_file():
        cached = json.loads(ALIGN_CACHE.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            print(f"Tag alignment: cached ({ALIGN_CACHE})")
            return cached["letters"]
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "tag.wav"
        wav.write_bytes(clip)
        letters = (align or aligner.align)(wav, TAG_LINE)
    ALIGN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ALIGN_CACHE.write_text(json.dumps({"key": key, "letters": letters}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Tag alignment: aligned and cached in {ALIGN_CACHE}")
    return letters


def merge_letters(body: list[dict], tag: list[dict], spoken: str, tag_start_s: float) -> list[dict]:
    """The reel's letters: the body's, then the tag's shifted to where the tag starts
    in `spoken` (the script's whole spoken text, ending with TAG_LINE) and in time."""
    if not spoken.endswith(PARAGRAPH_SEP + TAG_LINE):
        raise TagAudioError(f"The spoken text doesn't end with the tag paragraph {TAG_LINE!r}")
    offset = len(spoken) - len(TAG_LINE)
    shifted = [
        {
            **letter,
            "index": letter["index"] + offset,
            "start": round(letter["start"] + tag_start_s, 3),
            "end": round(letter["end"] + tag_start_s, 3),
        }
        for letter in tag
    ]
    bad = [s for s in shifted if spoken[s["index"]] != s["char"]]
    if bad:
        raise TagAudioError(f"Cached tag letters don't match {TAG_LINE!r}; delete {ALIGN_CACHE} and render again")
    return [*body, *shifted]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pick", type=int, metavar="N", default=None, help=f"pin take N as {TAG_AUDIO.relative_to(REPO_ROOT)} (no TTS)"
    )
    args = parser.parse_args()
    try:
        if args.pick is not None:
            pick(args.pick)
            return
        notifier = get_notifier()  # before TTS: don't pay for takes we can't send
        settings = load_settings()
        paths, cost = generate_takes(connection("tts"), settings.voice_id)
        print(f"TTS: {len(paths)} takes, ${cost:.4f}; saved in {TAKES_DIR}")
        send_takes(notifier, paths)
        print("Sent. Pin one with: python scripts/tag_audio.py --pick N")
    except (TagAudioError, TTSError, SendError, ConfigError, NotifyError) as exc:
        sys.exit(f"FAIL: {exc}")


if __name__ == "__main__":
    main()
