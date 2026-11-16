"""Smoke test: synthesize one Georgian line with the host's stored voice.

Writes out/smoke.wav. Credentials come only from the env file via load_settings().
"""

from __future__ import annotations

from reelline.config.credentials import load_settings
from reelline.media.tts import TTSError, call_row, synthesize
from reelline.paths import REPO_ROOT
from reelline.pipeline.db import manual_recording, record_manual

STYLE = "upbeat, friendly news host, crisp and clear, lifts on surprising facts, short pauses between stories"
TEXT = (
    "გამარჯობა, მე ყვითო ვარ! დღეს გეტყვით, რომელ საგანმანათლებლო პროგრამებზე "
    "შეგიძლიათ განაცხადის შეტანა ამ კვირაში, და ერთ ქართულ სტარტაპზე, "
    "რომელმაც ყველა გააკვირვა."
)
OUT_FILE = REPO_ROOT / "out" / "smoke.wav"


def main() -> None:
    settings = load_settings()
    try:
        speech = synthesize(None, TEXT, settings.voice_id, STYLE)  # the tts role (models.tts)
    except TTSError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
    with manual_recording():
        row = call_row(speech.input_tokens, speech.output_tokens, "tts_smoke")
    record_manual([row])
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_bytes(speech.wav)
    print(f"OK: wrote {OUT_FILE} ({len(speech.wav)} bytes, RIFF header present)")


if __name__ == "__main__":
    main()
