import io
import wave

import numpy as np
from reelline.media.tts import KEEP_SILENCE_S, clean_clip, concat_wavs, cost_usd

RATE = 24000


def to_wav(samples: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return out.getvalue()


def from_wav(data: bytes) -> tuple[int, np.ndarray]:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def test_clean_clip_trims_edge_silence_and_keeps_format():
    rng = np.random.default_rng(0)
    t = np.arange(RATE) / RATE  # 1 s tone
    tone = 0.3 * np.sin(2 * np.pi * 220 * t)

    def hiss(s):
        return 0.0005 * rng.standard_normal(round(s * RATE))

    rate, out = from_wav(clean_clip(to_wav(np.concatenate([hiss(0.4), tone, hiss(0.8)]))))
    assert rate == RATE
    # Tone plus about KEEP_SILENCE_S at each end (window granularity allows some slack).
    assert abs(len(out) / RATE - (1.0 + 2 * KEEP_SILENCE_S)) < 0.05


def test_clean_clip_tempo_shortens_the_clip():
    t = np.arange(RATE) / RATE
    wav = to_wav(0.3 * np.sin(2 * np.pi * 220 * t))
    _, plain = from_wav(clean_clip(wav))
    _, fast = from_wav(clean_clip(wav, tempo=1.25))
    assert abs(len(fast) / len(plain) - 1 / 1.25) < 0.02


def test_concat_inserts_silent_gaps():
    a, b = to_wav(np.full(RATE // 10, 0.5)), to_wav(np.full(RATE // 5, 0.5))
    rate, out = from_wav(concat_wavs([a, b], 0.25))
    assert len(out) == RATE // 10 + RATE // 4 + RATE // 5
    assert not out[RATE // 10 : RATE // 10 + RATE // 4].any()


def test_cost():
    assert cost_usd(1_000_000, 0) == 0.5
    assert cost_usd(0, 1_000_000) == 9.0


# --- the opening ---------------------------------------------------------------------------


def _measure(wav: bytes) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) of WAV bytes (ffmpeg loudnorm's measurement)."""
    import json
    import re
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.wav"
        path.write_bytes(wav)
        log = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True,
            text=True,
        ).stderr
    m = json.loads(re.findall(r"\{[^{}]*\}", log)[-1])
    return float(m["input_i"]), float(m["input_tp"])


def speechlike(seconds: float, seed: int = 1) -> np.ndarray:
    """A peaky, voice-like test signal: harmonics under a syllable-rate envelope."""
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds * RATE)) / RATE
    f0 = 150 * (1 + 0.1 * np.sin(2 * np.pi * 0.7 * t))
    phase = 2 * np.pi * np.cumsum(f0) / RATE
    voice = sum(np.sin(k * phase) / k for k in range(1, 12))
    envelope = np.clip(np.sin(2 * np.pi * 4 * t), 0, 1) ** 0.5
    return 0.5 * voice * envelope / np.abs(voice).max() + 0.002 * rng.standard_normal(len(t))


def test_boost_is_louder_under_the_same_true_peak_ceiling():
    from reelline.media.tts import LOUDNORM, OPENING_BOOST_DB

    wav = to_wav(speechlike(2.5))
    plain_i, plain_tp = _measure(clean_clip(wav, 1.1))
    hot_i, hot_tp = _measure(clean_clip(wav, 1.1, OPENING_BOOST_DB))
    assert 1.0 <= hot_i - plain_i <= OPENING_BOOST_DB + 0.1
    assert hot_tp <= LOUDNORM["TP"] + 0.05
    assert plain_tp <= LOUDNORM["TP"] + 0.05


def test_the_voice_starts_within_a_tenth_of_a_second():
    # A take with half a second of noise floor before the first word: after cleaning
    # (the reel's tempo, and the boosted opening sentence) the voice starts <= 0.1 s in.
    from reelline.pipeline.qa import QA

    rng = np.random.default_rng(2)
    lead = 0.0005 * rng.standard_normal(RATE // 2)
    wav = to_wav(np.concatenate([lead, speechlike(1.5)]))
    floor = 10 ** (QA["silence_db"] / 20)
    for out in (clean_clip(wav, 1.1), clean_clip(wav, 1.1, 3.0)):
        rate, samples = from_wav(out)
        first = np.argmax(np.abs(samples.astype(float)) / 32768 > floor) / rate
        assert first <= QA["voice_start_max_s"], first


def test_opening_text():
    from reelline.media.tts import opening_text

    assert opening_text("სტუდენტებს შანსი აქვთ.", "outrage") == "<gasp> სტუდენტებს შანსი აქვთ!"
    assert opening_text("ერთი „ციტატა.“", "disgust") == "ერთი „ციტატა!“"
    assert opening_text("რატომ?", "disgust tsk") == "<tsk> რატომ?!"
    assert opening_text("უკვე!", "disgust") == "უკვე!"
    assert opening_text("ბოლო...", "disgust") == "ბოლო!"
    assert opening_text("უწერტილო", "disgust") == "უწერტილო!"


def test_the_gemini_client_times_out_every_request(monkeypatch):
    from types import SimpleNamespace

    from google.genai._gaos.google_genai import build_google_genai_client
    from reelline.config import conf, credentials
    from reelline.providers import google

    monkeypatch.setattr(credentials, "load_gemini", lambda: SimpleNamespace(gemini_api_key="k"))
    settings = conf().models.providers.google
    client = google.connect(settings)
    assert settings.timeout_s == 120 and client._api_client._http_options.timeout == 120_000
    # TTS goes through interactions, whose client takes the same timeout.
    assert build_google_genai_client(client._api_client).sdk_configuration.timeout_ms == 120_000
