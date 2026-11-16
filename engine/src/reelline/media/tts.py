"""TTS with the host's stored voice, plus clip cleanup and WAV concatenation.

synthesize() makes one TTS call through the tts role's provider (models.tts; reelline/providers/google.py)
and returns the WAV bytes with the token usage; tts_style() is the host's one base style plus a
paragraph's @style nuance (the opening emotion's first sentence is OPENING_DELIVERY's style
instead, opening_text() its text); cost_usd() prices that usage; clean_clip()
trims a clip's edge silence, loudness-normalizes it, optionally speeds it up and
boosts it under the same true-peak ceiling; concat_wavs() joins clips with silent gaps.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from reelline.config import conf
from reelline.providers import LLMUsage, Speech, TTSError, get_tts
from reelline.providers import cost_usd as _cost_usd
from reelline.providers.costs import call_row as _call_row

__all__ = ["Speech", "TTSError"]  # re-exported: the TTS error and clip type live in reelline/providers/base.py
# the host's one voice, used for every TTS call; a paragraph's @style is appended
# as a nuance (see tts_style).
BASE_STYLE = (
    "fast, sharp, snarky and a bit bitchy; sarcastic, confident, high energy; crisp "
    "delivery; never whispering, never sleepy, never slurred, never slow"
)


# The opening emotion (reelline/media/script_markup.py OPENING_EMOTIONS) -> how paragraph 1's
# first sentence is voiced: `style` REPLACES BASE_STYLE (appended, the emotion stayed
# controlled), `tag` (a VOCAL_TAGS name or None) goes before the sentence, and the
# sentence ends with "!" (opening_text). Everything after it is BASE_STYLE (+ its own
# @style). Picked by ear on 3 takes per variant of reels 4-6's openers (2026-10-03,
# round 2; outrage without its gasp was dropped).
@dataclass(frozen=True)
class OpeningDelivery:
    style: str
    tag: str | None = None


OPENING_DELIVERY = {
    "outrage": OpeningDelivery("furious, yelling, loud, exasperated", "gasp"),
    "disgust": OpeningDelivery("disgusted, loud, contemptuous"),
    "disgust tsk": OpeningDelivery("disgusted, loud, contemptuous", "tsk"),
}
# The first sentence stays this much louder than the rest instead of normalized flat:
# boosted after loudnorm, then a LIMITER_OVERSAMPLE-x oversampled limiter keeps it
# LIMITER_MARGIN_DB under LOUDNORM's TP, so the voice track's peaks (and the final
# mix's normalization) are as before. Measured on the 2026-10-03 takes: 3 dB in comes
# out about +1.5..+2.4 dB louder (integrated).
OPENING_BOOST_DB = 3.0
LIMITER_OVERSAMPLE = 4
LIMITER_MARGIN_DB = 0.3

# Edge trimming: 20 ms RMS windows below this level count as silence (the TTS
# noise floor is about -50..-65 dBFS); KEEP_SILENCE_S of it stays at each end.
SILENCE_THRESHOLD_DB = -45
KEEP_SILENCE_S = 0.05
# Two-pass (linear) loudnorm target, the same for every clip.
LOUDNORM = {"I": -16.0, "TP": -1.5, "LRA": 11.0}


def tts_style(nuance: str | None) -> str:
    return BASE_STYLE if nuance is None else f"{BASE_STYLE}; for this line: {nuance}"


_SENTENCE_END = re.compile(r"[.\u2026]+([\u201c\u201d\"\u00bb)]*)$")


def opening_text(sentence: str, emotion: str) -> str:
    """Paragraph 1's first sentence (TTS text) as the opening emotion voices it: its
    variant's vocal tag first, and ending with "!" ("." or "..." becomes "!", "?" becomes
    "?!", closing quotes stay after it)."""
    text = sentence.strip()
    if _SENTENCE_END.search(text):
        text = _SENTENCE_END.sub(lambda m: "!" + m.group(1), text)
    elif re.search(r"\?[\u201c\u201d\"\u00bb)]*$", text):
        text = re.sub(r"\?([\u201c\u201d\"\u00bb)]*)$", lambda m: "?!" + m.group(1), text)
    elif not re.search(r"![\u201c\u201d\"\u00bb)]*$", text):
        text += "!"
    tag = OPENING_DELIVERY[emotion].tag
    return f"<{tag}> {text}" if tag else text


def tts_model() -> str:
    """The TTS model (models.tts.model): part of a take's cache key (scripts/render_reel.py)."""
    return conf().models.tts.model


def synthesize(client, text: str, voice: str, style: str) -> Speech:
    """One clip; `client`: the tts provider (get_tts()) or its connection."""
    return get_tts("tts", client).synthesize(text, voice=voice, style=style)


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    return _cost_usd(tts_model(), LLMUsage(input_tokens, output_tokens, 0))


def call_row(input_tokens: int, output_tokens: int, purpose: str = "tts") -> dict:
    """One TTS call's cost row for reelline/pipeline/db.py record_calls() (purpose tts, tts_reroll,
    tts_tag, tts_smoke), stamped now."""
    return _call_row(
        purpose, tts_model(), 0, LLMUsage(input_tokens, output_tokens, 0), cost_usd(input_tokens, output_tokens)
    )


def _ffmpeg(args: list[str]) -> str:
    if shutil.which("ffmpeg") is None:
        raise TTSError("ffmpeg not found on PATH; needed to clean TTS clips")
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise TTSError(f"ffmpeg failed:\n{result.stderr}")
    return result.stderr


def _trim_filter() -> str:
    # silenceremove only trims the start, so trim, reverse, trim again, reverse back.
    head = (
        f"silenceremove=start_periods=1:start_threshold={SILENCE_THRESHOLD_DB}dB"
        f":start_silence={KEEP_SILENCE_S}:detection=rms"
    )
    return f"{head},areverse,{head},areverse"


def _boost_filter(boost_db: float, rate: int) -> str:
    """+boost_db, then a limiter at LOUDNORM's TP minus LIMITER_MARGIN_DB, oversampled so
    its sample-peak limit holds the true peak too."""
    limit = 10 ** ((LOUDNORM["TP"] - LIMITER_MARGIN_DB) / 20)
    return (
        f"aresample={rate * LIMITER_OVERSAMPLE},volume={boost_db}dB,"
        f"alimiter=limit={limit:.4f}:attack=2:release=60:level=disabled,aresample={rate}"
    )


def clean_clip(wav: bytes, tempo: float = 1.0, boost_db: float = 0.0) -> bytes:
    """Trim edge silence to KEEP_SILENCE_S, normalize loudness to LOUDNORM, then
    time-stretch by `tempo` (ffmpeg atempo: >1 is faster, pitch preserved), then, with
    boost_db > 0, make it that much louder under the same true-peak ceiling (the
    opening sentence: OPENING_BOOST_DB).

    Keeps the clip's sample rate and channel count; output is 16-bit PCM WAV.
    """
    if not 0.5 <= tempo <= 2.0:
        raise TTSError(f"tempo must be within 0.5..2.0, got {tempo}")
    with wave.open(io.BytesIO(wav), "rb") as w:
        rate, channels = w.getframerate(), w.getnchannels()
    target = ":".join(f"{k}={v}" for k, v in LOUDNORM.items())
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.wav", Path(tmp) / "out.wav"
        src.write_bytes(wav)
        # Pass 1: measure the trimmed clip.
        log = _ffmpeg(
            [
                "-i",
                str(src),
                "-af",
                f"{_trim_filter()},loudnorm={target}:print_format=json",
                "-f",
                "null",
                "-",
            ]
        )
        blocks = re.findall(r"\{[^{}]*\}", log)
        if not blocks:
            raise TTSError(f"No loudnorm measurement in ffmpeg output:\n{log}")
        m = json.loads(blocks[-1])
        # Pass 2: apply one linear gain from the measurement, then the tempo.
        measured = (
            f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
        )
        boost = f",{_boost_filter(boost_db, rate)}" if boost_db > 0 else ""
        _ffmpeg(
            [
                "-y",
                "-i",
                str(src),
                "-af",
                f"{_trim_filter()},loudnorm={target}:{measured},atempo={tempo}{boost}",
                "-ar",
                str(rate),
                "-ac",
                str(channels),
                "-c:a",
                "pcm_s16le",
                str(dst),
            ]
        )
        return dst.read_bytes()


def concat_wavs(clips: list[bytes], gap_s: float) -> bytes:
    """One WAV: the clips in order with `gap_s` of silence between them.

    All clips must share channels, sample width and rate.
    """
    if not clips:
        raise TTSError("No clips to concatenate")
    params, frames = None, []
    for k, clip in enumerate(clips):
        with wave.open(io.BytesIO(clip), "rb") as w:
            p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if params is None:
                params = p
            elif p != params:
                raise TTSError(f"Clip {k} is {p} (channels, width, rate), clip 0 is {params}")
            frames.append(w.readframes(w.getnframes()))
    channels, width, rate = params
    gap = b"\x00" * (round(gap_s * rate) * channels * width)

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(gap.join(frames))
    return out.getvalue()
