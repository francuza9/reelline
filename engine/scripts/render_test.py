"""Lip-sync + captions test render: out/smoke.wav -> out/test_reel.mp4 (+ a PNG still).

Usage: python scripts/render_test.py [--mood MOOD] [--background NAME] [--still-time SECONDS]

Steps:
  1. Forced alignment of the smoke script text (TEXT from scripts/smoke_tts.py)
     against out/smoke.wav -> out/letters.json, and mouth cues -> out/mouth_cues.json
  2. Caption chunks from the text and the letter alignment -> out/captions.json
  3. Per-frame loudness of the audio for the talking bounce (reelline/media/loudness.py)
  4. Stage the images, backgrounds, fonts and audio into out/remotion_public/
     (Remotion's public dir; assets/ is only read, never written)
  5. Write props { audioSrc, mouthCues, anchors, mood, loudness, captions, background,
     cards, screenImages } and run `npx remotion render Reel`, then `npx remotion still Reel` for
     out/test_still_<background>.png
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from reelline.config import conf
from reelline.paths import REPO_ROOT, SCRIPTS_DIR
from reelline.render.registry import get_rig
from reelline.render.remotion import run_cli

sys.path.insert(0, str(SCRIPTS_DIR))

from captions import CaptionError, build_captions
from lipsync import LipsyncError, run_lipsync
from reelline.media.backgrounds import (
    BackgroundError,
    all_backgrounds,
    enabled_backgrounds,
    get_background,
    screen_spec,
    validate,
)
from reelline.media.loudness import frame_loudness
from smoke_tts import TEXT as SMOKE_TEXT

ASSETS = conf().channel.dir / "assets"
OUT = REPO_ROOT / "out"

INPUT_WAV = OUT / "smoke.wav"
LETTERS_JSON = OUT / "letters.json"
CUES_JSON = OUT / "mouth_cues.json"
CAPTIONS_JSON = OUT / "captions.json"
PUBLIC_DIR = OUT / "remotion_public"
PROPS_JSON = OUT / "reel_props.json"
OUTPUT_MP4 = OUT / "test_reel.mp4"

AUDIO_NAME = "smoke.wav"
# Fonts the Captions layer loads from the public dir (see remotion/src/Captions.tsx).
FONTS = tuple(conf().channel.fonts)
FPS = 30

# One headline card; the wall screen draws it only with SCENE.showCards (remotion/src/Scene.tsx).
TEST_CARDS = [{"type": "headline", "text": "ახალი სტიპენდიები", "start": 0.3, "end": 4.0}]


def load_anchors() -> dict:
    return json.loads(get_rig().anchors.read_text(encoding="utf-8"))


def moods(anchors: dict) -> list[str]:
    return [m for m in anchors["mouth"]["rest_by_mood"] if m != "note"]


def stage_public_dir(background) -> None:
    if PUBLIC_DIR.exists():
        shutil.rmtree(PUBLIC_DIR)
    PUBLIC_DIR.mkdir(parents=True)
    (PUBLIC_DIR / "fonts").mkdir()
    (PUBLIC_DIR / "backgrounds").mkdir()
    shutil.copy2(background.path, PUBLIC_DIR / "backgrounds" / background.file)
    get_rig().stage(PUBLIC_DIR)
    for font in FONTS:
        shutil.copy2(ASSETS / "fonts" / font, PUBLIC_DIR / "fonts" / font)
    shutil.copy2(INPUT_WAV, PUBLIC_DIR / AUDIO_NAME)


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def default_still_time(captions: list[dict]) -> float:
    """Middle of the middle word of the chunk with the most words (mid-sentence)."""
    chunk = max(captions, key=lambda c: sum(len(line) for line in c["lines"]))
    words = [w for line in chunk["lines"] for w in line]
    word = words[len(words) // 2]
    return (word["start"] + word["end"]) / 2


def remotion(args: list[str]) -> None:
    result = run_cli([*args, f"--props={PROPS_JSON}", f"--public-dir={PUBLIC_DIR}"])
    if result.returncode != 0:
        sys.exit(f"FAIL: remotion {args[0]} exited with {result.returncode}")


def main() -> None:
    anchors = load_anchors()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--mood", choices=moods(anchors), default="smug", help="rest mouth shown while not speaking (default: smug)"
    )
    parser.add_argument(
        "--background",
        choices=[b.name for b in all_backgrounds()],
        default=None,
        help="scene image from assets/backgrounds/ (default: the first enabled one by name)",
    )
    parser.add_argument(
        "--still-time",
        type=float,
        default=None,
        help="time (s) of the PNG still (default: mid-word of the longest chunk)",
    )
    args = parser.parse_args()
    try:
        enabled = enabled_backgrounds()
        background = get_background(args.background or (enabled[0].name if enabled else ""))
        validate(background)
        spec = screen_spec(background)
    except BackgroundError as exc:
        sys.exit(f"FAIL: {exc}")
    args.background = background.name

    try:
        cues = run_lipsync(INPUT_WAV, CUES_JSON, method="align", text=SMOKE_TEXT, letters_json=LETTERS_JSON)
    except LipsyncError as exc:
        sys.exit(f"FAIL: {exc}")
    print(f"Lip sync: {CUES_JSON} ({len(cues['mouthCues'])} cues)")

    letters = json.loads(LETTERS_JSON.read_text(encoding="utf-8"))
    try:
        captions = build_captions(SMOKE_TEXT, letters)
    except CaptionError as exc:
        sys.exit(f"FAIL: {exc}")
    CAPTIONS_JSON.write_text(json.dumps(captions, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Captions: {CAPTIONS_JSON} ({len(captions)} chunks)")

    stage_public_dir(background)
    props = {
        "audioSrc": AUDIO_NAME,
        "mouthCues": cues["mouthCues"],
        "anchors": anchors,
        "mood": args.mood,
        "loudness": frame_loudness(INPUT_WAV, FPS),
        "captions": captions,
        "background": background.file,
        "screen": spec,
        "cards": TEST_CARDS,
        "screenImages": [],
        "outro": None,
    }
    PROPS_JSON.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    remotion(["render", "Reel", str(OUTPUT_MP4)])
    still_time = args.still_time if args.still_time is not None else default_still_time(captions)
    still_frame = round(still_time * FPS)
    output_still = OUT / f"test_still_{args.background}.png"
    remotion(["still", "Reel", str(output_still), f"--frame={still_frame}"])

    print(f"Output: {OUTPUT_MP4} (mood {args.mood}, background {args.background})")
    print(f"Duration: {probe_duration(OUTPUT_MP4):.2f}s")
    print(f"Still: {output_still} (frame {still_frame}, {still_time:.2f}s)")


if __name__ == "__main__":
    main()
