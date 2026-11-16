"""Render a reel from a marked-up script: text -> TTS -> lip sync + captions + cards -> MP4.

Usage: python scripts/render_reel.py <script.txt> [--background NAME] [--mood MOOD]
                                     [--still-time SECONDS] [--fresh-tts] [--regen N ...]
                                     [--music TRACK|auto|none] [--no-images]
                                     [--refresh-images] [--reimage N ...] [--no-outro]
                                     [--no-sfx] [--sfx-gain DB] [--publish-date YYYY-MM-DD]
                                     [--fx-unchecked] [--gag auto|none|NAME]
                                     [--no-cover | --cover-only] [--cover-debug] [--send]
       python scripts/render_reel.py --background-check NAME

render() is the importable entry point (reelline/pipeline/produce.py): RenderOptions in (the
flags below), RenderResult out, RenderError on any failure; main() is a thin CLI
over it that prints FAIL: <message> and exits 1.

Background (reelline/media/backgrounds.py): --background NAME, else the first enabled
background by name (the pipeline passes its rotation's pick). The PNG is validated
and only it is staged; its sidecar, if any, is merged over remotion/src/screen.json
and goes to Remotion as the `screen` prop and to the image picker (face size).
--background-check NAME renders a still (a silent track, no TTS: the TV on a sample
library photo, the host in DEFAULT_MOOD, a sample two-line caption) and a cover with a
sample headline for any background, disabled ones too, into
out/background_check/<name>/, sends both to Telegram and prints the merged screen
geometry.

<script.txt> uses the markup in reelline/media/script_markup.py: an optional @cover first
line, then paragraphs with optional @style / @card / @image lines, {display|spoken}
groups and <vocal tags>, and last the tag paragraph (TAG_LINE alone, no directives).

Wall-TV images (channel docs/images.md): a paragraph's @image picture shows from its first
word until the next picture (a further "@image: query | word" line of the paragraph, or the
next paragraph's first word; a slot with no match keeps the previous picture); through the details line (@details, no image
of its own) the picture before it holds, and through the tag paragraph the last one holds
until the outro starts. Picks are cached in <name>/images.json
and reused while the paragraph's @image value is unchanged; --refresh-images ignores
that cache, --reimage N re-picks paragraph N only (excluding its earlier picks),
new picks skip images shown in recent approved/published reels (the cooldown, from the
database) and logo queries for organizations the script and its brief
(content/briefs/<name>.md) don't name, --no-images leaves the TV on its idle face (and
removes images.json / attributions.txt, which would otherwise describe images the reel
doesn't show).

Headline cards (cards.json, and the `cards` prop; the TV only draws them when
remotion/src/Scene.tsx SCENE.showCards is on, off by default): if the script has
@card lines, each card starts at its paragraph's
first word. Otherwise they come from <script>.cards.json next to it, if present:
  [{"text": "...", "at": "<display word>", "occurrence": 1}, ...]
where a card starts at the `occurrence`-th (default first) display token equal to
"at" (punctuation at the token's ends ignored). Either way a card stays until the
next card starts, and the last one until the outro (or the end).

Outro (reelline/media/outro.py, remotion/src/outro.json): the end card (remotion/src/EndCard.tsx)
comes in over the host's last line: it irises open leadS before the first word of the line
startAt names (the tag line; or the share line), his voice goes on under it, and after the
voice ends it holds holdS with music only. A frame of its own (no room, no host, no TV, and
no captions: they stop where it starts, reelline/media/outro.py captions_before(); captions.json keeps
them all): the opportunity's facts from <script>.endcard.json next to the script
(reelline/pipeline/endcard.py writes it; checked before any TTS, reelline/media/outro.py card_problems()), and
the footer. Without the file the card has the footer only (a NOTE says so). The music is
ducked under the voice as everywhere, raised by musicLiftDb once the voice has ended, and
fades out at the end. --no-outro leaves it out.

Share arrow and the platform versions (reelline/media/share.py, remotion/src/share.json): while
The host says the share line (@share) a hand-drawn arrow points at the platform's own share
button (remotion/src/ShareArrow.tsx), so each platform gets its own file,
<name>/reel_<platform>.mp4 (instagram, tiktok, youtube, facebook), and reel.mp4 is the
review's (Instagram's). The frames before and after the arrow are rendered once, without it;
only the arrow's stretch is rendered once per platform, and each platform's three segments
are joined by stream copy over one AAC encode of the mix (render_versions()). Without a
share line (or with share.json "on" false) the reel is rendered once and the four files are
the same file. versions.json in the work dir records the segments, their render times and
the checks of each join.

Fact graphics (channel docs/fx.md, reelline/media/fx.py): a paragraph's @fx card pops out of the TV from its
anchor word (else the paragraph's first word), holds, and flies back; the room behind it is
dimmed and the captions stay on one line while it's out. Only the ones the fact-check
supported are shown: <script>.fx.json next to the script (reelline/pipeline/fx.py writes it) lists
them; without the file none is shown (a NOTE says so), --fx-unchecked shows every @fx
anyway (a WARNING says so; for hand-written scripts). A card is back in the TV by the next
paragraph (a too-short paragraph: minHoldS into the next one if it has no joke), cut short
before the next card and the end card, and left out when it would then hold too briefly.
Paragraph 1's card is the frame-0 card (fx.json opening): out and settled from the first
frame, at least opening.minHoldS, with no TV click.

Motion (reelline/media/motion.py, remotion/src/motion.json): the camera cuts between a wide, a
medium and a close shot every 4-6 s on a sentence or clause boundary (framing; a close shot
never while a card is out, a push-in runs or a new picture is up), and now and then, on a
sentence's end, to another camera angle (framing.angles, reelline/media/angles.py: the room from 3/4
for 20-30% of the body, short beats from behind the host as a new picture comes up; front while
a card is out or the share arrow is up; the background's 3/4 art and the rigs are staged into
remotion_public/angles/); a push-in on the host landing
on each punchline (the sentence before a laugh tag, the closing jab), a reaction pose and a
squash-and-stretch bounce on each, a glance up at the TV when its picture changes or a fact
graphic pops out, an eye-stalk reaction to each paragraph's @style (else the mood), a lean-in
on emphasis (the reveal after <short pause>, a number, a '!'); no push-in or reaction during
a fact graphic or the end card. A stretch longer than beats.maxGapS (the first
beats.windowS seconds) or beats.body.maxGapS (the rest) without a visual change gets a
push-in or a glance up at the TV (the beats). motion_events.json in the work dir has them,
the beat map and what was left out; the render log prints the shots and the beat map. The voice track opens on its first sound (LEAD_IN_S). The deadline's
days-left tag counts from the publish date: --publish-date, else the date a pipeline
script's file name starts with (its slot), else none. fx_events.json in the work dir has
the cards and what was left out.

Cold open (channel docs/gags.md, reelline/media/gags.py, remotion/src/gags.json): none in the straight style
(reelline/media/style.py STYLE_MODE, since 2026-10-31: no gag, the MP4 is the reel from its frame 0). In the
classic style, with gags.json mode "on", every reel ("ab" a seeded half by the reel's name, "off" none)
opens on a 1.5 s gag of the host caught doing something embarrassing, a seeded pick none of the last 8 reels had (the database's reels), with
its action sound and a record scratch, then a hard cut into the reel. The reel itself doesn't move:
Remotion plays it from the gag's last frame (remotion/src/Reel.tsx), every time in the props stays
the reel's own, and the MP4 is the gag longer; its audio is the gag's sound followed by the mix
(gag_mix.wav), so the voice, the music and the SFX start at the cut. --gag auto (default) decides
as above, none leaves it out, a gag's name forces that one. With --no-sfx the gag is silent.

Cover (remotion/src/Cover.tsx): after the reel, a 1080x1920 still for the profile
grid: the reel's background, the TV on the reel's first image (idle without one),
The host at rest, and the @cover headline (paragraph 1's @card if there's no @cover;
no headline at all fails before TTS). --no-cover skips it (and removes an old
cover.png, which --send would otherwise send). --cover-only re-renders just the
cover from the last render's reel_props.json and remotion_public/ (no TTS, no reel
render; --mood / --background override the reel's). --cover-debug writes
cover_debug.png instead, with the profile grid's safe area drawn on it.

Background music: --music auto (default) makes a weighted pick from
assets/music/music.json, seeded by the reel name so re-renders keep the same track;
a track name (file stem) forces one, none leaves it out. Mixing is in reelline/media/music.py;
the level is MUSIC_LEVEL (--music-level), and on the end card musicLiftDb more.

Sound effects (channel docs/sfx.md, the channel's sfx): a TV click at each wall-TV content change, a
TV power sound when the TV first shows an image and at the outro, and a sting after
it, from the licensed library in assets/sfx/, mixed into the audio at their gains
relative to the voice (reelline/media/sfx/config.py). --sfx-gain DB shifts them all for quick
tuning; --no-sfx leaves them out (and removes an old sfx.json). An empty category
the reel needs fails the render before TTS.

Outputs for <name>.txt, all under out/reels/:
  <name>.wav (voice only)
  <name>/reel.mp4           the video, the review's: Instagram's version (and <name>/still.png
                            with --still-time)
  <name>/reel_<platform>.mp4  each platform's version (instagram, tiktok, youtube, facebook):
                            its share arrow; versions.json what was rendered and joined
  <name>/cover.png          the cover (cover_props.json its props)
  <name>/mix.wav            voice + music + SFX, the MP4's audio (unless there's
                            neither music nor SFX, or a cold open)
  <name>/gag.wav, gag_mix.wav, gag.json   with a cold open: its sound, the MP4's audio (the gag's
                            sound, then mix.wav or the voice), and the decision (gag.json also
                            without one: which arm and why)
  <name>/sfx.json           the SFX events: event, time, sound id, gain (dB, vs the voice)
  <name>/tts/pNN.wav|json   raw per-paragraph TTS, reused while text/voice/style match
                            (not for a pinned tag; its alignment is out/tag_align.json)
  <name>/letters.json, mouth_cues.json, captions.json, cards.json, reel_props.json
  <name>/check_these.json   the report's low-score words (scripts/send_to_phone.py reads it)
  <name>/fx_events.json     the fact graphics shown (Remotion's fx prop) and the ones left out
  <name>/images.json, attributions.txt   wall-TV image picks and caption credit lines
                            (image lines, then an SFX section: reelline/media/sfx/credits.py)
  <name>/remotion_public/   staged Remotion public dir

Steps:
  1. Parse and validate the markup (including the closing tag paragraph); check the
     spoken text is alignable, every card anchor exists, there's a cover headline and
     the end card's facts file (if any) is valid, all before any TTS call.
  2. TTS per paragraph (voice from settings, style BASE_STYLE plus the
     paragraph's @style as a nuance). The tag paragraph uses the pinned
     voice/tag.wav instead when it exists (scripts/tag_audio.py), else TTS (the
     render says which). Each clip is trimmed, loudness-normalized and sped up by
     TTS_TEMPO (reelline/media/tts.py clean_clip), then they're joined with GAP_S gaps. The
     cache keeps the raw clips, so tempo/gap changes are free.
  3. Forced alignment of the final (stretched, joined) voice track's spoken text ->
     letters -> mouth cues (scripts/lipsync.py). With a pinned tag, only the body is
     aligned; the tag's letters come from its cached alignment, shifted into place.
     Alignment, lip sync, captions and the talking bounce all use the voice-only track.
  4. Captions from the display tokens, timed through the markup mapping; the
     outro's place on the last line, the share line's.
  4a. Images: resolve each paragraph's @image (the channel's images), write images.json and
     attributions.txt, print one line per paragraph; this run's paid image-model calls
     (vision checks, generated illustrations) go out in RenderResult.image_calls, its
     fresh TTS clips in RenderResult.tts_calls. A failure carries what was spent so far
     (the exception's paid_calls). Run from this CLI, the calls are recorded in
     llm_calls tagged manual (produce records its own renders).
  4b. SFX events from the screen images' timeline, the words and the outro (sfx.json).
  4c. Music and SFX under the finished voice track, running on through the outro (mix.wav).
  5. Remotion render (same composition as scripts/render_test.py), with the mix
     as the audio; then the cover still. Without a share arrow Remotion writes the final
     MP4 itself (no ffmpeg step after it); with one, the platform versions are rendered in
     segments and joined (render_versions()). Either way the video is encoded as
     remotion/encode.json says (JPEG 95 frames, H.264
     at 12 Mbps, yuv420p BT.709 TV range, faststart; QA checks it). A failed
     Remotion run's RenderError names the composition (Reel or Cover) and ends with
     the last REMOTION_ERROR_LINES lines of its stderr; the rest of its output
     (progress, the code frame) is on stdout.
  6. Report, including a duration check (the MP4 must run to the outro's end)
     and a "check these" list of words the aligner scored low (likely
     mispronounced; re-roll a paragraph with --regen N).
  7. With --send, the reel goes to Telegram (scripts/send_to_phone.py).
"""

from __future__ import annotations

import argparse
import array
import hashlib
import io
import json
import math
import re
import shutil
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from reelline import language
from reelline.paths import REPO_ROOT, SCRIPTS_DIR

sys.path.insert(0, str(SCRIPTS_DIR))

from align import AlignError, align, romanize_letters
from align import release as release_aligner
from captions import CaptionError, chunk_words, join_untimed
from lipsync import LipsyncError, run_lipsync, write_cues
from reelline.config import conf
from reelline.config.credentials import ConfigError, load_settings
from reelline.media import (
    gags,
    style,
)
from reelline.media.angles import AngleError
from reelline.media.angles import available as available_angles
from reelline.media.angles import check_files as check_angle_files
from reelline.media.angles import files as angle_files
from reelline.media.backgrounds import (
    BACKGROUNDS,
    Background,
    BackgroundError,
    all_backgrounds,
    enabled_backgrounds,
    get_background,
    screen_spec,
    validate,
)
from reelline.media.fx import FX_SUFFIX, FxError, caption_windows, placed, planned, publish_date_of
from reelline.media.fx import config as fx_config
from reelline.media.gags import GagError
from reelline.media.images.config import CANDIDATES, Screen, screen_from_spec
from reelline.media.images.cooldown import recently_shown_in
from reelline.media.images.framing import cover_crop, resolution_problem
from reelline.media.images.library import Library
from reelline.media.images.resolve import ImageError, Picker, resolve_reel, summary_line
from reelline.media.images.sources import SourceError
from reelline.media.images.vision import VisionError
from reelline.media.loudness import frame_loudness
from reelline.media.motion import MotionError
from reelline.media.motion import plan as plan_motion
from reelline.media.motion import summary as motion_summary
from reelline.media.music import FADE_OUT_S, MusicError, MusicLift, SfxHit, load_catalog, mix, pick_track
from reelline.media.outro import (
    OutroError,
    captions_before,
    card_start,
    facts_path,
    load_facts,
    load_outro,
    ordered,
    outro_window,
)
from reelline.media.renderlock import RenderLockTimeout, render_lock
from reelline.media.script_markup import (
    PARAGRAPH_SEP,
    MarkupError,
    Script,
    card_units,
    cover_headline,
    find_token,
    image_slots,
    opener_animation,
    parse,
    split_first_sentence,
    time_tokens,
)
from reelline.media.sfx.config import CATEGORIES as SFX_CATEGORIES
from reelline.media.sfx.config import CREDITS as SFX_CREDITS
from reelline.media.sfx.config import FX_POP as SFX_FX_POP
from reelline.media.sfx.config import TV_POWER as SFX_TV_POWER
from reelline.media.sfx.credits import write_credits
from reelline.media.sfx.events import Plan, check_outro_fit, empty_category_message
from reelline.media.sfx.events import plan as plan_sfx
from reelline.media.sfx.library import SfxError, by_category, load_library
from reelline.media.share import (
    ShareError,
    platform_file,
    share_props,
    share_span,
)
from reelline.media.share import load as load_share
from reelline.media.tts import (
    OPENING_BOOST_DB,
    OPENING_DELIVERY,
    SILENCE_THRESHOLD_DB,
    TTSError,
    clean_clip,
    concat_wavs,
    cost_usd,
    opening_text,
    synthesize,
    tts_model,
    tts_style,
)
from reelline.media.tts import call_row as tts_call_row
from reelline.notify import NotifyError
from reelline.providers import connection
from reelline.render import remotion as _remotion
from reelline.render.base import RenderError
from reelline.render.registry import get_rig
from render_test import (
    ASSETS,
    FONTS,
    FPS,
    load_anchors,
    moods,
    probe_duration,
)
from send_to_phone import COVER_FILE, SendError, send_photo, send_reel
from tag_audio import TAG_AUDIO, TagAudioError, load_pinned, merge_letters, tag_letters

OUT_DIR = REPO_ROOT / "out" / "reels"
CHECK_DIR = REPO_ROOT / "out" / "background_check"
COVER_DEBUG_FILE = "cover_debug.png"
DEFAULT_MOOD = conf().steps.produce.default_mood

# Speed-up applied to every clip after trimming and loudnorm (pitch preserved).
TTS_TEMPO = 1.1
# Background music loudness relative to the voice (LU), before ducking; the one
# music volume knob (--music-level overrides it per render). Ducking, fades and the
# final loudness target are in reelline/media/music.py; on the end card the bed is raised by
# remotion/src/outro.json musicLiftDb more. -14.8 since 2026-10-23 (was -16.0: +15%,
# about +1.2 dB; with the lift's 1.1 dB the card's music is about +30%, +2.3 dB).
MUSIC_LEVEL = -14.8
# Silence between paragraph clips (after trimming and the tempo change).
GAP_S = 0.2
# The first wall-TV picture starts at 0 (remotion/src/opening.ts OPENING.imageFromFrame0
# draws it already faded in at frame 0).
FIRST_IMAGE_FROM_0 = True
# ...unless the first picture only comes this many seconds in (paragraphs before it found no
# image): then the TV stays idle until it, not that picture for the whole stretch.
FIRST_IMAGE_MAX_PULL_S = 6.0
# Silence between an opening paragraph's first sentence and the rest (each clip keeps
# its trimmed KEEP_SILENCE_S edges too).
OPENING_GAP_S = 0.15
# The voice track's lead-in: the reel opens mid-word, so of the first clip's KEEP_SILENCE_S
# edge (~40 ms after the tempo change, frame 0 silent) only this much stays before its
# first sound (trim_lead(): LEAD_WINDOW_S RMS windows under SILENCE_THRESHOLD_DB).
LEAD_IN_S = 0.01
LEAD_WINDOW_S = 0.005
# Words whose mean letter score is below this are listed under "check these".
LOW_SCORE = 0.3
# The rendered MP4 may differ from the planned length (speech, or the outro's end) by
# at most this much (frame rounding, audio padding); more fails the render.
DURATION_TOLERANCE_S = 0.1
# The motion pass's lists Remotion draws (remotion/src/types.ts MotionProps); motion_events.json
# has them plus what was left out and the beats.
MOTION_PROPS = ("zooms", "reactions", "glances", "shots", "leans", "stalks", "bounces")


def card_anchors(script: Script, script_path: Path) -> tuple[list[tuple[str, int]], str]:
    """[(card text, display token index)] and where the cards came from.

    Raises MarkupError for a missing or out-of-order anchor.
    """
    if any(p.card for p in script.paragraphs):
        cards = [(p.card, script.first_token(n)) for n, p in enumerate(script.paragraphs) if p.card]
        return cards, "@card lines"
    path = script_path.with_suffix(".cards.json")
    if not path.is_file():
        return [], "none"
    spec = json.loads(path.read_text(encoding="utf-8"))
    cards = [(c["text"], find_token(script, c["at"], c.get("occurrence", 1))) for c in spec]
    for (text_a, a), (text_b, b) in zip(cards, cards[1:], strict=False):
        if b <= a:
            raise MarkupError(f"Card {text_b!r} is anchored before or at card {text_a!r}")
    return cards, path.name


def build_cards(script: Script, anchors: list[tuple[str, int]], words: list[dict], body_end: float) -> list[dict]:
    """Headline cards from (text, token index): each starts at the first timed token
    from its anchor on (same paragraph) and ends where the next card starts, the last
    one at `body_end` (the outro's start)."""
    starts = []
    for text, i in anchors:
        paragraph = script.tokens[i].paragraph
        j = next(
            (
                k
                for k in range(i, len(words))
                if script.tokens[k].paragraph == paragraph and words[k]["start"] is not None
            ),
            None,
        )
        if j is None:
            raise MarkupError(f"Card {text!r}: no aligned word at or after its anchor in paragraph {paragraph + 1}")
        starts.append(words[j]["start"])
    ends = starts[1:] + [body_end]
    return [
        {"type": "headline", "text": text, "units": card_units(script, text), "start": s, "end": round(e, 3)}
        for (text, _), s, e in zip(anchors, starts, ends, strict=False)
    ]


def paragraph_starts(script: Script, words: list[dict]) -> list[float | None]:
    """Start time of each paragraph's first timed display token (None if it has none)."""
    starts: list[float | None] = [None] * len(script.paragraphs)
    for token, word in zip(script.tokens, words, strict=False):
        if starts[token.paragraph] is None and word["start"] is not None:
            starts[token.paragraph] = word["start"]
    return starts


def screen_starts(script: Script, words: list[dict]) -> list[float | None]:
    """paragraph_starts() for the wall TV: the details line and the tag paragraph (never an
    image of their own) are no screen boundary, so the previous paragraph's image, or idle,
    holds through them (through the tag until `body_end`, the outro's start, where the TV
    goes idle with the tv_off sound)."""
    return [
        None if para.tag or para.details else start
        for para, start in zip(script.paragraphs, paragraph_starts(script, words), strict=False)
    ]


def slot_starts(script: Script, words: list[dict]) -> dict[tuple[int, int], float | None]:
    """(paragraph, slot) -> when each wall-TV picture starts: slot 0 at screen_starts(); an
    extra @image (script_markup.image_slots()) at its anchor word, or the first timed word
    after it (None if none is timed)."""
    para = screen_starts(script, words)
    out: dict[tuple[int, int], float | None] = {}
    for ref in image_slots(script):
        if ref.slot == 0:
            out[ref.paragraph, 0] = para[ref.paragraph - 1]
            continue
        out[ref.paragraph, ref.slot] = next(
            (
                w["start"]
                for t, w in zip(script.tokens[ref.token :], words[ref.token :], strict=False)
                if t.paragraph == ref.paragraph - 1 and w["start"] is not None
            ),
            None,
        )
    return out


def too_close_slots(
    starts: dict[tuple[int, int], float | None], body_end: float, min_s: float = CANDIDATES["min_slot_s"]
) -> set[tuple[int, int]]:
    """Extra pictures that would be on for under `min_s` seconds, or start under `min_s`
    after the picture before them: resolve_reel() skips them (no search, no vision call),
    so the TV doesn't flicker."""
    timed = sorted((t, key) for key, t in starts.items() if t is not None)
    out: set[tuple[int, int]] = set()
    last = None  # start of the last picture kept
    for i, (t, key) in enumerate(timed):
        nxt = timed[i + 1][0] if i + 1 < len(timed) else body_end
        if key[1] > 0 and ((last is not None and t - last < min_s) or nxt - t < min_s):
            out.add(key)
            continue
        last = t
    return out


def screen_images(
    manifest: dict,
    starts: list[float | None],
    body_end: float,
    public_dir: Path | None,
    library: Library,
    slots: dict[tuple[int, int], float | None] | None = None,
) -> list[dict]:
    """Remotion screenImages from the manifest: each picture from its first word to the
    next one that changes the TV (the next picture's, or an explicit `none` paragraph's;
    the last one to `body_end`: the outro's start, so the TV is idle in the outro). A slot
    with no match (or skipped) changes nothing: the previous picture holds through it, and
    the TV is idle only before the first picture. `starts` are the paragraphs' (screen_starts),
    `slots` every picture's (slot_starts(); needed for extra pictures). Copies the files
    into the public dir under screen_images/ (not with `public_dir` None: the timeline
    only). The render builds this list once; the Remotion props and the SFX planner both
    use it."""
    changes: list[tuple[float, dict]] = []  # (start, entry) of every picture and `none`
    for entry in manifest["paragraphs"]:
        if entry["status"] not in ("image", "none"):
            continue
        n, k = entry["paragraph"], entry.get("slot", 0)
        start = (slots or {}).get((n, k), starts[n - 1] if k == 0 else None)
        if start is None:
            if entry["status"] == "image":
                raise ImageError(f"¶{n} has no aligned word to start its image at")
            continue
        changes.append((start, entry))
    changes.sort(key=lambda c: c[0])
    out = []
    for i, (start, entry) in enumerate(changes):
        if entry["status"] != "image":
            continue
        n, k = entry["paragraph"], entry.get("slot", 0)
        end = changes[i + 1][0] if i + 1 < len(changes) else body_end
        src = library.root / entry["file"]
        if not src.is_file():
            raise ImageError(f"¶{n}: library file missing: {src}")
        out.append(
            {
                "src": f"screen_images/{src.name}",
                "start": start,
                "end": round(end, 3),
                "width": entry["width"],
                "height": entry["height"],
                "mode": entry["mode"],
                "crop": entry["crop"],
                "focal": entry["focal"],
                "panelColor": entry["panel_color"],
                "seed": f"{entry['image_id']}:{n}" + (f".{k}" if k else ""),
            }
        )
    if public_dir is not None:
        copy_screen_images(manifest, public_dir, library)
    return out


def first_image_from_0(images: list[dict]) -> list[dict]:
    """`images` with the first one starting at 0 (FIRST_IMAGE_FROM_0), if it starts within
    FIRST_IMAGE_MAX_PULL_S: past that the TV stays idle until it."""
    if FIRST_IMAGE_FROM_0 and images and 0 < images[0]["start"] <= FIRST_IMAGE_MAX_PULL_S:
        return [{**images[0], "start": 0.0}, *images[1:]]
    return images


def copy_screen_images(manifest: dict, public_dir: Path, library: Library) -> None:
    """The manifest's image files into the public dir under screen_images/."""
    for entry in manifest["paragraphs"]:
        if entry["status"] == "image":
            src = library.root / entry["file"]
            dest = public_dir / "screen_images" / src.name
            dest.parent.mkdir(exist_ok=True)
            shutil.copy2(src, dest)


def tag_span(cleaned: list[bytes], wav: Path) -> tuple[float, float]:
    """(start, end) of the tag line's clip (the last paragraph) in the voice track."""
    total_frames, rate = wav_frames(wav.read_bytes())
    return (total_frames - wav_frames(cleaned[-1])[0]) / rate, total_frames / rate


def sfx_needed(no_images: bool, outro: bool, fx: bool = False) -> list[str]:
    """SFX categories a render will draw from: clicks need the TV images (or a fact graphic,
    with FX_POP on), the sting the outro, the TV power sound either (TV_POWER on / off)."""
    power = (not no_images and SFX_TV_POWER["on"]) or (outro and SFX_TV_POWER["off"])
    clicks = not no_images or (fx and SFX_FX_POP["on"])
    return [c for c, on in (("tv_click", clicks), ("tv_power", power), ("sting", outro)) if on]


def fx_shown(script: Script, script_path: Path, unchecked: bool) -> tuple[set[tuple[int, str]] | None, str]:
    """The @fx the render may show ((paragraph, raw) pairs; None: every one) and a line saying
    which: the fact-check's file <script>.fx.json (reelline/pipeline/fx.py), else none; --fx-unchecked: all."""
    at = [(n, p.fx.raw) for n, p in enumerate(script.paragraphs, 1) if p.fx is not None]
    if not at:
        return set(), "Fx: none in the script"
    if unchecked:
        return None, f"Fx: WARNING: --fx-unchecked, showing all {len(at)} @fx without a fact-check"
    path = script_path.with_suffix(FX_SUFFIX)
    if not path.is_file():
        return set(), f"Fx: NOTE: {len(at)} @fx but no {path.name} (the fact-check's); none shown"
    try:
        checked = json.loads(path.read_text(encoding="utf-8"))
        shown = {(int(f["paragraph"]), f["raw"]) for f in checked["shown"]}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RenderError(f"Can't read {path}: {exc}") from None
    stale = [f"¶{n}" for n, raw in at if (n, raw) not in shown]
    return shown, (
        f"Fx: {len(at) - len(stale)} of {len(at)} @fx fact-checked as supported ({path.name})"
        + (f"; not shown: {', '.join(stale)}" if stale else "")
    )


def fx_pop_starts(events: list[dict]) -> list[float]:
    """When cards spring out of the TV, for their click (not the frame-0 card: it's already out)."""
    return [e["start"] for e in events if not e.get("frame0")]


def fx_summary(events: list[dict], left: list[dict]) -> list[str]:
    """Console lines: each card shown (paragraph, template, value, window), each one left out and why."""
    from reelline.media.fx import show

    lines = [f"Fx: {len(events)} card(s)" + (f", {len(left)} left out" if left else "")]
    lines += [
        f"     ¶{e['paragraph']} {e['data']['template']} „{show(e['data'])}“ {e['start']:.2f}-{e['end']:.2f}s"
        + (" (frame 0)" if e.get("frame0") else "")
        + (f" (days left: {e['data']['daysLeft']})" if e["data"].get("daysLeft") else "")
        for e in events
    ]
    lines += [f"     left out: ¶{x['paragraph']} {x['raw']}: {x['reason']}" for x in left]
    return lines


def cold_open(name: str, force: str, sounds: list, no_sfx: bool) -> tuple:
    """gags.json and the reel's cold open (reelline/media/gags.py decide(): the A/B by the reel's name, the last
    reels' gags from the database, not read for a forced one or with mode off), its art and (unless
    no_sfx) its sounds checked, all before any TTS. Returns (the config, the Decision)."""

    try:
        cfg = gags.load()
        recent = (
            gags.recent_gags_in(conf().storage.db_path, name, cfg.no_repeat)
            if force == "auto" and gags.mode_of(cfg) != "off"
            else []
        )
        decision = gags.decide(name, recent, cfg, force)
        if decision.gag:
            gags.check_files(cfg, [decision.gag])
            if not no_sfx:
                gags.check_sounds(cfg, sounds, [decision.gag])
    except GagError as exc:
        raise RenderError(f"Cold open: {exc}") from None
    print(
        f"Cold open: {decision.gag or 'none'} ({decision.why})"
        + (", silent (--no-sfx)" if decision.gag and no_sfx else "")
    )
    return cfg, decision


def face_size(spec: dict) -> tuple[float, float]:
    """The wall TV's face (width, height) in output pixels, from the merged screen spec."""
    scr = screen_from_spec(spec)
    return scr.face_w, scr.face_h


def gag_sounds(cfg, decision: gags.Decision, sounds: list) -> list:
    """The SFX library sounds the cold open plays (its action sound, the scratch), for the credits."""
    if not decision.gag or not sounds:
        return []
    by_id = {s.id: s for s in sounds}
    return [by_id[cfg.gags[decision.gag].sound], by_id[cfg.scratch]]


def sfx_summary(sfx_plan: Plan, capped: list[str]) -> list[str]:
    """Console lines: clicks, TV on/off, the sting's time, and every lowered, moved or capped event."""
    clicks = sfx_plan.clicks
    lines = [
        f"SFX: {len(clicks)} TV click(s) for {sfx_plan.changes} screen change(s)"
        + (f", {sfx_plan.gap_skipped} too close to the previous click (no click)" if sfx_plan.gap_skipped else "")
        + f"; {sfx_plan.lowered} event(s) lowered under speech"
    ]
    for e in sfx_plan.power:
        lines.append(
            f"     TV {e.event.removeprefix('tv_')}: {e.sound.id} at {e.time:.2f}s" + (f" ({e.note})" if e.note else "")
        )
    if sfx_plan.fx:
        lines.append(
            f"     fact graphics: {len(sfx_plan.fx)} click(s) at " + ", ".join(f"{e.time:.2f}s" for e in sfx_plan.fx)
        )
    sting = sfx_plan.sting
    lines.append(f"     sting: {sting.sound.id} at {sting.time:.2f}s" if sting else "     sting: none (no outro)")
    lines += [f"     moved: {m}" for m in sfx_plan.moved]
    lines += [f"     capped at the peak ceiling: {c}" for c in capped]
    return lines


def opening_props(script: Script) -> dict:
    """Remotion's `opening` prop: the opener animation for paragraph 1's style (every
    script has one: reelline/media/script_markup.py opener_animation()) and the opening emotion
    (QA's timing rules read it from reel_props.json)."""
    return {"burst": True, "animation": opener_animation(script), "emotion": script.opening}


def outro_props(outro: dict | None, facts: dict[str, str] | None, cfg, script: Script) -> dict | None:
    """Remotion's `outro` prop: the window, the end card's facts in the card's order, and
    whether the script points to the card (a details line: QA fails a card without facts then)."""
    if outro is None:
        return None
    return {**outro, "facts": ordered(facts or {}, cfg), "details": any(p.details for p in script.paragraphs)}


def pad_loudness(loudness: list[float], length_s: float) -> list[float]:
    """Per-frame loudness padded with silence to `length_s` (the outro runs past the voice)."""
    return loudness + [0.0] * max(0, math.ceil(length_s * FPS) - len(loudness))


@dataclass(frozen=True)
class OpeningClip:
    """Paragraph 1's raw audio when it has an opening emotion: its first sentence in
    that emotion, and the rest (None if the paragraph is one sentence) in the base voice."""

    first: bytes
    rest: bytes | None


def clean_paragraph(clip: bytes | OpeningClip) -> bytes:
    """One paragraph's raw TTS -> its cleaned clip (reelline/media/tts.py clean_clip at TTS_TEMPO); an
    opening paragraph's first sentence boosted by OPENING_BOOST_DB, joined to the rest
    with OPENING_GAP_S."""
    if not isinstance(clip, OpeningClip):
        return clean_clip(clip, TTS_TEMPO)
    first = clean_clip(clip.first, TTS_TEMPO, OPENING_BOOST_DB)
    return first if clip.rest is None else concat_wavs([first, clean_clip(clip.rest, TTS_TEMPO)], OPENING_GAP_S)


def trim_lead(wav: bytes, keep_s: float = LEAD_IN_S) -> bytes:
    """The clip (16-bit PCM WAV) without its leading silence but `keep_s`: everything before
    the first LEAD_WINDOW_S window whose RMS reaches SILENCE_THRESHOLD_DB. A silent clip is
    returned as is."""
    with wave.open(io.BytesIO(wav), "rb") as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    if params.sampwidth != 2:
        raise RenderError(f"trim_lead: expected 16-bit PCM, got {8 * params.sampwidth}-bit")
    samples = array.array("h", frames)
    if sys.byteorder == "big":
        samples.byteswap()
    channels, rate = params.nchannels, params.framerate
    window = max(1, round(LEAD_WINDOW_S * rate)) * channels
    floor = 32768 * 10 ** (SILENCE_THRESHOLD_DB / 20)
    first = next(
        (
            i
            for i in range(0, len(samples), window)
            if math.sqrt(sum(x * x for x in samples[i : i + window]) / len(samples[i : i + window])) >= floor
        ),
        None,
    )
    if first is None:
        return wav
    cut = max(0, first // channels - round(keep_s * rate)) * channels
    if cut == 0:
        return wav
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(frames[cut * 2 :])
    return out.getvalue()


def paragraph_audio(
    client_factory,
    script: Script,
    voice: str,
    tts_dir: Path,
    regen: set[int],
    pinned_tag: bytes | None = None,
    spent: list[dict] | None = None,
):
    """Raw WAV bytes per paragraph, reusing cached clips unless the paragraph
    (1-based) is in `regen` or its text/voice/style changed. The tag paragraph
    gets `pinned_tag` when given (never TTS, whatever `regen` says). Each fresh
    clip's cost row (reelline/media/tts.py call_row()) goes to `spent` as it's made.

    A paragraph with an opening emotion (paragraph 1) is two TTS calls: its first
    sentence as the emotion voices it (reelline/media/tts.py OPENING_DELIVERY's style instead of
    the base one, opening_text(): its vocal tag first, "!" at the end) and the rest with
    the base style (pNN.wav, pNNb.wav), returned as an OpeningClip.

    Returns (clips, usage rows); a pinned tag's row has pinned=True and no tokens.
    """
    tts_dir.mkdir(parents=True, exist_ok=True)
    clips, usage = [], []
    client = None
    total = len(script.paragraphs)

    def take(n: int, part: str, text: str, style: str, what: str = "") -> tuple[bytes, dict]:
        nonlocal client
        key = hashlib.sha256(json.dumps([tts_model(), voice, style, text]).encode()).hexdigest()
        wav_path, meta_path = tts_dir / f"p{n:02d}{part}.wav", tts_dir / f"p{n:02d}{part}.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        if n not in regen and wav_path.is_file() and meta.get("key") == key:
            return wav_path.read_bytes(), {**meta, "cached": True}
        client = client or client_factory()
        print(f"TTS paragraph {n}/{total}{what} ({len(text)} chars)...", flush=True)
        speech = synthesize(client, text, voice, style)
        if spent is not None:
            spent.append(tts_call_row(speech.input_tokens, speech.output_tokens))
        meta = {"key": key, "input_tokens": speech.input_tokens, "output_tokens": speech.output_tokens}
        wav_path.write_bytes(speech.wav)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return speech.wav, {**meta, "cached": False}

    for n, para in enumerate(script.paragraphs, start=1):
        if para.tag and pinned_tag is not None:
            clips.append(pinned_tag)
            usage.append({"input_tokens": 0, "output_tokens": 0, "cached": True, "pinned": True})
            continue
        if para.opening is None:
            wav, meta = take(n, "", para.tts, tts_style(para.style))
            clips.append(wav)
            usage.append(meta)
            continue
        first, rest = split_first_sentence(para.tts)
        opener = opening_text(first, para.opening)
        parts = [take(n, "", opener, OPENING_DELIVERY[para.opening].style, f" first sentence, {para.opening}")]
        if rest is not None:
            parts.append(take(n, "b", rest, tts_style(None), " rest"))
        clips.append(OpeningClip(parts[0][0], parts[1][0] if rest is not None else None))
        usage.append(
            {
                "input_tokens": sum(m["input_tokens"] for _, m in parts),
                "output_tokens": sum(m["output_tokens"] for _, m in parts),
                "cached": all(m["cached"] for _, m in parts),
            }
        )
    return clips, usage


def wav_frames(wav: bytes) -> tuple[int, int]:
    """(frame count, sample rate) of WAV bytes."""
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.getnframes(), w.getframerate()


def lipsync_pinned_tag(script: Script, cleaned: list[bytes], wav: Path, work: Path) -> dict:
    """Mouth cues (and letters.json) for the voice track when the tag clip is pinned:
    the body (every paragraph but the tag) is aligned as usual, the tag's letters
    come from tag_letters() (cached), shifted to where its clip starts in `wav`."""
    body_text = PARAGRAPH_SEP.join(p.spoken for p in script.paragraphs[:-1])
    with tempfile.TemporaryDirectory() as tmp:
        body_wav = Path(tmp) / "body.wav"
        body_wav.write_bytes(concat_wavs(cleaned[:-1], GAP_S))
        body = align(body_wav, body_text)
    tag = tag_letters(cleaned[-1])
    total_frames, rate = wav_frames(wav.read_bytes())
    tag_start = (total_frames - wav_frames(cleaned[-1])[0]) / rate
    letters = merge_letters(body, tag, script.spoken, tag_start)
    return write_cues(wav, work / "mouth_cues.json", letters, work / "letters.json")


def check_these(script: Script, letters: list[dict]) -> list[dict]:
    """Spoken words whose mean letter score is below LOW_SCORE, in spoken order."""
    by_index = {letter["index"]: letter for letter in letters}
    rows = []
    for m in re.finditer(r"\S+", script.spoken):
        word_letters = [by_index[i] for i in range(m.start(), m.end()) if i in by_index]
        if not word_letters:
            continue
        score = sum(letter["score"] for letter in word_letters) / len(word_letters)
        if score < LOW_SCORE:
            rows.append(
                {
                    "paragraph": script.paragraph_at_spoken(m.start()) + 1,
                    "start": word_letters[0]["start"],
                    "word": m.group(),
                    "score": round(score, 3),
                }
            )
    return rows


def stage_public_dir(public_dir: Path, wav: Path, audio_name: str, background: Background) -> None:
    if public_dir.exists():
        shutil.rmtree(public_dir)
    public_dir.mkdir(parents=True)
    (public_dir / "fonts").mkdir()
    stage_background(public_dir, background)
    get_rig().stage(public_dir)
    # The camera's other angles (reelline/media/angles.py): the rigs, their mouths and this room's 3/4 art,
    # at their paths under assets/.
    for path in angle_files(background.name):
        dest = public_dir / path.relative_to(ASSETS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    for font in FONTS:
        shutil.copy2(ASSETS / "fonts" / font, public_dir / "fonts" / font)
    shutil.copy2(wav, public_dir / audio_name)


def stage_background(public_dir: Path, background: Background) -> None:
    """The one background the render uses, under backgrounds/ in the public dir."""
    (public_dir / "backgrounds").mkdir(parents=True, exist_ok=True)
    shutil.copy2(background.path, public_dir / "backgrounds" / background.file)


def load_background(name: str) -> tuple[Background, dict]:
    """The background (validated, its camera angles' art too) and its merged screen spec."""
    try:
        bg = get_background(name)
        validate(bg)
        check_angle_files(bg.name)
        return bg, screen_spec(bg)
    except (BackgroundError, AngleError) as exc:
        raise RenderError(str(exc)) from None


def cover_props(
    script: Script | None,
    headline: str,
    anchors: dict,
    mood: str,
    background: str,
    screen: dict,
    image: dict | None,
    debug: bool,
) -> dict:
    """Props of the Cover still; `background` is a file name, `screen` its merged spec,
    `image` a screenImages entry. Without a script the headline's words are its units."""
    units = card_units(script, headline) if script is not None else headline.split()
    return {
        "anchors": anchors,
        "mood": mood,
        "background": background,
        "screen": screen,
        "image": image,
        "headline": headline,
        "units": units,
        "debugSafeArea": debug,
    }


def render_cover(work: Path, props: dict, public_dir: Path) -> Path:
    """Render the Cover still into `work` (cover_debug.png with the debug overlay)."""
    props_json = work / "cover_props.json"
    props_json.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    out = work / (COVER_DEBUG_FILE if props["debugSafeArea"] else COVER_FILE)
    remotion(["still", "Cover", str(out)], props_json, public_dir)
    return out


def remotion(args: list[str], props_json: Path | None, public_dir: Path | None, bundle: Path | None = None) -> None:
    """The Remotion CLI (reelline/render/remotion.py)."""
    _remotion.remotion(args, props_json, public_dir, bundle)


def render_versions(
    work: Path,
    props: dict,
    props_json: Path,
    public_dir: Path,
    audio: Path,
    reel_end: float,
    share_cfg,
    gag_frames: int = 0,
) -> dict:
    """reel.mp4 and each platform's version (reelline/render/remotion.py render_versions())."""
    return _remotion.render_versions(work, props, props_json, public_dir, audio, reel_end, share_cfg, gag_frames, FPS)


@dataclass
class RenderOptions:
    """render()'s knobs; the CLI flags of the same names. background None: the first
    enabled background by name; mood None: the style's (reelline/media/style.py: straight's happy), else DEFAULT_MOOD."""

    background: str | None = None
    mood: str | None = None
    still_time: float | None = None
    fresh_tts: bool = False
    regen: list[int] = field(default_factory=list)
    music: str = "auto"
    music_level: float = MUSIC_LEVEL
    no_images: bool = False
    refresh_images: bool = False
    reimage: list[int] = field(default_factory=list)
    no_outro: bool = False
    no_sfx: bool = False
    sfx_gain: float = 0.0
    no_cover: bool = False
    cover_only: bool = False
    cover_debug: bool = False
    send: bool = False
    # The deadline's days-left tag counts from it (None: the script file name's date, if any).
    publish_date: date | None = None
    fx_unchecked: bool = False
    # The cold open (reelline/media/gags.py decide()): auto (gags.json's mode, the A/B), none, or a gag's name.
    gag: str = "auto"


@dataclass
class RenderResult:
    name: str
    work: Path
    mp4: Path
    background: str
    mood: str
    cover: Path | None = None
    duration: float | None = None  # the rendered MP4's length (None: --cover-only)
    images_json: Path | None = None
    attributions: Path | None = None
    check_these: list[dict] = field(default_factory=list)
    tts_cost_usd: float | None = None  # the whole reel's TTS (cached clips at their original cost)
    tts_new_cost_usd: float | None = None  # this run's TTS calls only
    # This run's paid image-model calls (vision checks, generated illustrations):
    # reelline/media/images/vision.py call_row() dicts.
    image_calls: list[dict] = field(default_factory=list)
    # This run's TTS calls (fresh clips only): reelline/media/tts.py call_row() dicts.
    tts_calls: list[dict] = field(default_factory=list)
    # The fact graphics shown (Remotion FxEvent dicts) and the ones left out ({paragraph, raw, reason}).
    fx: list[dict] = field(default_factory=list)
    fx_left: list[dict] = field(default_factory=list)
    # Each platform's file (reelline/media/share.py PLATFORMS), reel.mp4 being Instagram's.
    versions: dict[str, Path] = field(default_factory=dict)
    # The cold open (reelline/media/gags.py): the gag (None: none), its length (where the reel starts in the
    # MP4), and the mode it was decided under (gags.json's, or "forced"); for reels.gag, gag_s, gag_mode.
    gag: str | None = None
    gag_s: float | None = None
    gag_mode: str | None = None


def default_background() -> str:
    try:
        enabled = enabled_backgrounds()
    except BackgroundError as exc:
        raise RenderError(str(exc)) from None
    if not enabled:
        raise RenderError("No enabled backgrounds in assets/backgrounds/")
    return enabled[0].name


def cover_only(work: Path, script: Script, headline: str, anchors: dict, opts: RenderOptions) -> RenderResult:
    """--cover-only: the cover from the last render's props and public dir."""
    props_json, public_dir = work / "reel_props.json", work / "remotion_public"
    if not props_json.is_file() or not public_dir.is_dir():
        raise RenderError(
            f"--cover-only needs {props_json.name} and {public_dir.name}/ in {work}; render the reel first"
        )
    reel = json.loads(props_json.read_text(encoding="utf-8"))
    image = reel["screenImages"][0] if reel["screenImages"] else None
    if image and not (public_dir / image["src"]).is_file():
        raise RenderError(f"--cover-only: {public_dir / image['src']} is missing; render the reel again")
    mood = opts.mood or reel["mood"]
    bg, spec = load_background(opts.background or Path(reel["background"]).stem)
    stage_background(public_dir, bg)
    out = render_cover(
        work, cover_props(script, headline, anchors, mood, bg.file, spec, image, opts.cover_debug), public_dir
    )
    print(f"Cover: {out} (mood {mood}, background {bg.name}, TV {image['src'] if image else 'idle'})")
    return RenderResult(name=work.name, work=work, mp4=work / "reel.mp4", background=bg.name, mood=mood, cover=out)


def render(script_path: Path, opts: RenderOptions | None = None) -> RenderResult:
    """Render the reel (and its cover) for a script file; raises RenderError. Whatever
    it raises carries `paid_calls`: the cost rows of the calls made before the failure
    (TTS clips, image checks), so the caller can still record them."""
    spent: dict[str, list[dict]] = {"tts": [], "images": []}
    try:
        return _render(script_path, opts or RenderOptions(), spent)
    except RenderLockTimeout as exc:
        err = RenderError(str(exc))
        err.paid_calls = spent["tts"] + spent["images"]
        raise err from None
    except BaseException as exc:
        exc.paid_calls = spent["tts"] + spent["images"]
        raise


def _render(script_path: Path, opts: RenderOptions, spent: dict[str, list[dict]]) -> RenderResult:
    anchors = load_anchors()
    try:
        tracks = {t.name: t for t in load_catalog()}
    except MusicError as exc:
        raise RenderError(str(exc)) from None
    if opts.mood is not None and opts.mood not in moods(anchors):
        raise RenderError(f"Unknown mood {opts.mood!r} (have: {', '.join(moods(anchors))})")
    if opts.music not in ("auto", "none", *tracks):
        raise RenderError(f"Unknown music {opts.music!r} (auto, none or a track: {', '.join(tracks)})")

    name = script_path.stem
    work = OUT_DIR / name
    work.mkdir(parents=True, exist_ok=True)
    wav = OUT_DIR / f"{name}.wav"
    mp4 = work / "reel.mp4"

    # 1. Everything that can fail without audio fails here, before TTS.
    try:
        script = parse(script_path.read_text(encoding="utf-8"))
        if language.current().lipsync_align:
            romanize_letters(script.spoken)
        cards_at, cards_source = card_anchors(script, script_path)
    except (MarkupError, AlignError) as exc:
        raise RenderError(f"{script_path}: {exc}") from None
    try:
        outro_cfg = None if opts.no_outro else load_outro()
        facts = load_facts(facts_path(script_path), outro_cfg) if outro_cfg else None
    except OutroError as exc:
        raise RenderError(str(exc)) from None
    try:
        fx_config()
    except FxError as exc:
        raise RenderError(str(exc)) from None
    try:
        share_cfg = load_share()
    except ShareError as exc:
        raise RenderError(str(exc)) from None
    fx_allowed, fx_line = fx_shown(script, script_path, opts.fx_unchecked)
    publish = opts.publish_date or publish_date_of(script_path)
    if opts.no_cover and opts.cover_debug:
        raise RenderError("--cover-debug needs a cover; drop --no-cover")
    headline = None
    if not opts.no_cover:
        try:
            headline, source = cover_headline(script)
        except MarkupError as exc:
            raise RenderError(f"{script_path}: {exc} (or render with --no-cover)") from None
        if source != "@cover":
            print(f"Cover: no @cover line; falling back to the {source}")
        print(f"Cover headline: {headline} (from {source})")
    if opts.cover_only:
        reel_only = [
            flag
            for flag, on in (
                ("--fresh-tts", opts.fresh_tts),
                ("--regen", opts.regen),
                ("--no-images", opts.no_images),
                ("--refresh-images", opts.refresh_images),
                ("--reimage", opts.reimage),
                ("--still-time", opts.still_time is not None),
                ("--no-outro", opts.no_outro),
                ("--no-sfx", opts.no_sfx),
                ("--sfx-gain", opts.sfx_gain != 0.0),
                ("--publish-date", opts.publish_date is not None),
                ("--fx-unchecked", opts.fx_unchecked),
                ("--gag", opts.gag != "auto"),
            )
            if on
        ]
        if reel_only:
            raise RenderError(f"--cover-only doesn't render the reel; drop {', '.join(reel_only)}")
        result = cover_only(work, script, headline, anchors, opts)
        if opts.send:
            try:
                send_reel(mp4)
            except (SendError, ConfigError, NotifyError) as exc:
                raise RenderError(f"--send: {exc}") from None
        return result
    if not language.current().lipsync_align:
        raise RenderError(
            f"language {language.current().code!r} has no lip sync and caption timing yet (reelline/language: "
            "lipsync_align): render the cover only (--cover-only)"
        )
    mood = opts.mood or style.current().mood or DEFAULT_MOOD  # straight: happy, not smug
    background, spec = load_background(opts.background or default_background())
    total = len(script.paragraphs)
    bad_regen = [n for n in opts.regen if not 1 <= n <= total]
    if bad_regen:
        raise RenderError(f"--regen {bad_regen[0]}: the script has paragraphs 1..{total}")
    regen = set(range(1, total + 1)) if opts.fresh_tts else set(opts.regen)
    for n in opts.reimage:
        image = script.paragraphs[n - 1].image if 1 <= n <= total else None
        if image is None or image.kind != "query":
            raise RenderError(
                f"--reimage {n}: "
                + (
                    f"the script has paragraphs 1..{total}"
                    if not 1 <= n <= total
                    else "that paragraph has no @image search query"
                )
            )
    if opts.no_images and (opts.reimage or opts.refresh_images):
        raise RenderError("--no-images can't be combined with --reimage / --refresh-images")
    if opts.no_sfx and opts.sfx_gain != 0.0:
        raise RenderError("--sfx-gain does nothing with --no-sfx; drop one")
    sounds = []
    if not opts.no_sfx:
        try:
            sounds = load_library()
        except SfxError as exc:
            raise RenderError(f"SFX library: {exc}") from None
        any_fx = fx_allowed is None or bool(fx_allowed)
        for category in sfx_needed(opts.no_images, outro_cfg is not None, any_fx):
            if not by_category(sounds, category):
                raise RenderError(empty_category_message(category))
        if outro_cfg is not None:
            try:
                check_outro_fit(outro_cfg.hold_s, FPS)
            except SfxError as exc:
                raise RenderError(f"SFX: {exc}") from None
    gag_cfg, gag = cold_open(name, opts.gag, sounds, opts.no_sfx)
    try:
        pinned_tag = load_pinned()
    except TagAudioError as exc:
        raise RenderError(str(exc)) from None
    if pinned_tag is None:
        print(
            f"Tag: {TAG_AUDIO} not found; paragraph {total} (the tag) falls back to TTS "
            "(pin a take with scripts/tag_audio.py)"
        )
    elif total in opts.regen:
        raise RenderError(
            f"--regen {total}: that's the tag paragraph, pinned to {TAG_AUDIO}; "
            "pick another take with scripts/tag_audio.py --pick N"
        )
    else:
        print(f"Tag: paragraph {total} uses the pinned {TAG_AUDIO}")
    print(
        f"Script: {total} paragraphs, {len(script.tokens)} display tokens, {len(cards_at)} cards (from {cards_source})"
    )
    if cards_source == "@card lines" and script_path.with_suffix(".cards.json").is_file():
        print(f"NOTE: ignoring {script_path.with_suffix('.cards.json').name}; the script has @card lines")
    print(fx_line + (f"; days left counted from {publish}" if publish and fx_allowed != set() else ""))
    print(f"Background: {background.name}" + (f" (sidecar {background.sidecar.name})" if background.sidecar else ""))
    try:
        track = (
            None
            if opts.music == "none" or (opts.music == "auto" and not tracks)  # no catalog: no music
            else (pick_track(list(tracks.values()), name) if opts.music == "auto" else tracks[opts.music])
        )
    except MusicError as exc:
        raise RenderError(str(exc)) from None
    print(
        f"Music: {track.name} ({track.group}, {'auto pick' if opts.music == 'auto' else 'forced'})"
        if track
        else "Music: none"
    )

    # 2. TTS, then trim + normalize each clip and join.
    try:
        settings = load_settings()
        clips, usage = paragraph_audio(
            lambda: connection("tts"),
            script,
            settings.voice_id,
            work / "tts",
            regen,
            pinned_tag,
            spent["tts"],
        )
        cleaned = [clean_paragraph(c) for c in clips]
        cleaned[0] = trim_lead(cleaned[0])  # the reel opens on its first sound
        wav.write_bytes(concat_wavs(cleaned, GAP_S))
    except (ConfigError, TTSError) as exc:
        raise RenderError(str(exc)) from None
    print(f"Audio: {wav}")

    # 3. Alignment + mouth cues.
    letters_json = work / "letters.json"
    try:
        if pinned_tag is None:
            cues = run_lipsync(
                wav, work / "mouth_cues.json", method="align", text=script.spoken, letters_json=letters_json
            )
        else:
            cues = lipsync_pinned_tag(script, cleaned, wav, work)
    except (LipsyncError, AlignError, TagAudioError) as exc:
        raise RenderError(str(exc)) from None
    finally:
        release_aligner()  # ~1.2 GB back before Remotion's bundle and Chrome
    letters = json.loads(letters_json.read_text(encoding="utf-8"))
    duration = cues["metadata"]["duration"]
    print(f"Lip sync: {len(letters)} letters, {len(cues['mouthCues'])} cues")

    # 4. Captions from display tokens, the outro on the last line; cards anchored to
    # display tokens. Cards, paragraph images and captions stop where the outro starts.
    try:
        timed = time_tokens(script, letters)
        # The fact graphics' cards first: the captions stay on one line while one is out.
        fx_cards, fx_left = planned(script, timed, fx_allowed)
        captions = chunk_words(join_untimed(timed), one_line=caption_windows(fx_cards))
        outro = None
        if outro_cfg:
            card_at, card_line = card_start(script, timed, outro_cfg)
            outro = outro_window(card_at, duration, outro_cfg, card_line)
        # The body is the reel before the outro; the reel runs to the outro's end.
        body_end = outro["start"] if outro else duration
        reel_end = outro["end"] if outro else duration
        span = share_span(script, timed)
        cards = build_cards(script, cards_at, timed, body_end)
        fx_events, cut = placed(fx_cards, body_end, publish)
        fx_left += cut
    except (MarkupError, CaptionError, FxError, OutroError) as exc:
        raise RenderError(str(exc)) from None
    (work / "fx_events.json").write_text(
        json.dumps(
            {"events": fx_events, "left": fx_left, "publish_date": publish.isoformat() if publish else None},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    (work / "captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=1), encoding="utf-8")
    (work / "cards.json").write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Captions: {len(captions)} chunks")
    if outro:
        print(
            f"Outro: the end card {outro['start']:.2f}-{outro['end']:.2f}s, in over the {outro['startAt']} line "
            f"(voice ends {outro['speechEnd']:.2f}s, then {outro_cfg.hold_s:g}s with music only)"
        )
        print(
            f"End card: {', '.join(facts)} (from {facts_path(script_path).name})"
            if facts
            else f"End card: NOTE: no facts ({facts_path(script_path).name} missing or empty); the footer only"
        )
    else:
        print("Outro: off (--no-outro)")
    share = share_props(span, share_cfg, None)
    if share:
        print(
            f"Share arrow: the share line {share['lineStart']:.2f}-{share['lineEnd']:.2f}s, the arrow "
            f"{share['start']:.2f}-{share['end']:.2f}s on each platform's version"
        )
    else:
        print(
            "Share arrow: none ("
            + ("share.json is off)" if span else "no share line)")
            + "; the four platform files are the same file"
        )
    if fx_events or fx_left:
        for line in fx_summary(fx_events, fx_left):
            print(line)

    # 4a. Wall-TV images, framed for this background's TV.
    image_calls: list[dict] = []
    if opts.no_images:
        manifest = None
        for stale in (work / "images.json", work / "attributions.txt"):
            if stale.exists():
                stale.unlink()
                print(f"Images: off (--no-images); removed {stale.name}")
        print("Images: off (--no-images), idle screen only")
    else:
        pickers: list[Picker] = []

        def new_picker() -> Picker:
            pickers.append(Picker(Library(), lambda: connection("vision"), scr=screen_from_spec(spec)))
            pickers[-1].calls = spent["images"]  # shared, so a failure still has them
            return pickers[-1]

        brief = conf().storage.briefs_dir / f"{name}.md"
        try:
            manifest = resolve_reel(
                script,
                work,
                new_picker,
                name,
                refresh=opts.refresh_images,
                reimage=set(opts.reimage),
                cooldown=recently_shown_in(conf().storage.db_path, REPO_ROOT, name),
                context=brief.read_text(encoding="utf-8") if brief.is_file() else "",
                skip=too_close_slots(slot_starts(script, timed), body_end),
            )
        except (ImageError, VisionError, SourceError, ConfigError) as exc:
            raise RenderError(f"images: {exc}") from None
        image_calls = list(spent["images"])
        print("Images:")
        for entry in manifest["paragraphs"]:
            print(summary_line(entry))
        used = manifest["generated_fallback_used"]
        tried = manifest["generated_fallback_tried"]
        print(
            f"Generated fallback: {'used for ' + ', '.join(f'¶{n}' for n in used) if used else 'not used'}"
            + (f" (tried this run for {', '.join(f'¶{n}' for n in tried)})" if tried else "")
        )
        if image_calls:
            print(f"Image model calls this run: {len(image_calls)}, ${sum(c['cost_usd'] for c in image_calls):.4f}")

    # 4b. The wall-TV timeline (Remotion's screenImages), and the SFX events from it.
    try:
        images = (
            []
            if manifest is None
            else screen_images(
                manifest, screen_starts(script, timed), body_end, None, Library(), slot_starts(script, timed)
            )
        )
    except ImageError as exc:
        raise RenderError(f"images: {exc}") from None
    # Paragraph 1's picture, or the first later one, from frame 0 (the SFX plan sees the TV's
    # first change at 0, so tv_on plays at the opening window's end).
    images = first_image_from_0(images)
    # 4b'. The motion pass (reelline/media/motion.py): the camera's shots, punchline zooms, reaction
    # poses, bounces, TV glances, stalk reactions, lean-ins, the beats.
    try:
        motion = plan_motion(
            script,
            timed,
            images,
            fx_events,
            body_end,
            fx_lead_s=float(fx_config()["placements"]["popout"]["leadS"]),
            mood=mood,
            anchors=anchors,
            screen=spec,
            arrow=(share["start"], share["end"]) if share else None,
            angles=available_angles(background.name),
        )
    except MotionError as exc:
        raise RenderError(str(exc)) from None
    (work / "motion_events.json").write_text(json.dumps(motion, ensure_ascii=False, indent=1), encoding="utf-8")
    for line in motion_summary(motion):
        print(line)
    sfx_json = work / "sfx.json"
    sfx_plan = None
    if opts.no_sfx:
        if sfx_json.exists():
            sfx_json.unlink()
            print(f"SFX: off (--no-sfx); removed {sfx_json.name}")
        else:
            print("SFX: off (--no-sfx)")
    else:
        try:
            sfx_plan = plan_sfx(
                sounds,
                name,
                FPS,
                images,
                body_end,
                timed,
                outro["start"] if outro else None,
                tag_span(cleaned, wav),
                reel_end,
                opts.sfx_gain,
                fx_starts=fx_pop_starts(fx_events),
            )
        except SfxError as exc:
            raise RenderError(f"SFX: {exc}") from None
    hits = [
        SfxHit(
            f"{e.event} {e.sound.id} at {e.time:.2f}s",
            e.sound.path,
            e.time,
            e.gain_db,
            e.sound.level_db,
            e.sound.peak_db,
            SFX_CATEGORIES[e.sound.category]["normalize"] == "peak",
        )
        for e in (sfx_plan.events if sfx_plan else [])
    ]

    # 4c. Music and SFX under the finished voice track and on through the outro; only
    # the video gets the mix.
    mix_wav = work / "mix.wav"
    m = None
    if track or hits:
        try:
            # Raised once the voice has ended (the card's hold), ducked under it as everywhere.
            lift = MusicLift(outro["speechEnd"], outro_cfg.music_lift_db, outro_cfg.music_lift_s) if outro else None
            m = mix(
                wav,
                track,
                mix_wav,
                f"{name}/{track.name}" if track else name,
                opts.music_level,
                length_s=reel_end,
                fade_out_s=outro_cfg.music_fade_s if outro_cfg else FADE_OUT_S,
                sfx=hits,
                lift=lift,
            )
        except MusicError as exc:
            raise RenderError(str(exc)) from None
        if track:
            print(
                f"Mix: {mix_wav} ({reel_end:.2f}s; {track.name} from {m.offset:.1f}s, {m.loops} loops, "
                f"level {opts.music_level:+g} LU, gain {m.gain_db:+.1f} dB, ducking -{m.duck_db:.1f} dB)\n"
                f"     final mix {m.out_lufs:.1f} LUFS, TP {m.out_tp:.1f} dB ({m.normalization} loudnorm); "
                f"in it: voice {m.voice_lufs:.1f} LUFS, music {m.music_lufs:.1f} LUFS "
                f"({m.music_lufs - m.voice_lufs:+.1f} LU)"
                + (
                    f"; on the end card {m.music_end_lufs:.1f} LUFS (+{outro_cfg.music_lift_db:g} dB lift)"
                    if m.music_end_lufs is not None
                    else ""
                )
            )
        else:
            print(
                f"Mix: {mix_wav} ({reel_end:.2f}s; voice and SFX, no music)\n"
                f"     final mix {m.out_lufs:.1f} LUFS, TP {m.out_tp:.1f} dB ({m.normalization} loudnorm); "
                f"in it: voice {m.voice_lufs:.1f} LUFS"
            )
    else:
        mix_wav.unlink(missing_ok=True)
    if sfx_plan is not None:
        applied = m.sfx if m else ()
        rows = [e.row() for e in sfx_plan.events]
        for row, a in zip(rows, applied, strict=False):
            if a.capped:
                row["note"] = "; ".join(filter(None, [row.get("note"), "capped at the peak ceiling"]))
        sfx_json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        for line in sfx_summary(sfx_plan, [a.label for a in applied if a.capped]):
            print(line)
        if m and m.sfx_tp is not None:
            print(f"     SFX peak in the final mix: {m.sfx_tp:.1f} dBTP (mix TP {m.out_tp:.1f})")
    # The SFX section of attributions.txt (none with --no-sfx), after the image lines; the cold open's first.
    credits = write_credits(
        work / "attributions.txt",
        gag_sounds(gag_cfg, gag, sounds) + ([e.sound for e in sfx_plan.events] if sfx_plan else []),
    )
    if credits:
        print(
            f"     credits: {len(credits)} line(s) in attributions.txt"
            f"{'' if SFX_CREDITS['in_captions'] else ' (not in captions: CREDITS in_captions is off)'}"
        )

    # 4d. The cold open: its sound, then the reel's audio from the cut (the MP4's audio).
    mixed = bool(track or hits)
    audio = mix_wav if mixed else wav
    audio_name = f"{name}_mix.wav" if mixed else f"{name}.wav"
    gag_s = gag_cfg.duration_s if gag.gag else 0.0
    gag_wav, gag_mix = work / "gag.wav", work / "gag_mix.wav"
    gag_record: dict = {"gag": gag.gag, "mode": gag.mode, "why": gag.why}
    if gag.gag:
        try:
            if opts.no_sfx:
                gags.silent_audio(gag_cfg, gag_wav)
                gag_record["sound"] = None
            else:
                gag_record["sound"] = gags.gag_audio(gag_cfg, gag.gag, sounds, gag_wav)
            gags.with_gag(gag_wav, audio, gag_mix)
        except GagError as exc:
            raise RenderError(f"Cold open: {exc}") from None
        audio, audio_name = gag_mix, f"{name}_gag_mix.wav"
        gag_record.update(durationS=gag_cfg.duration_s, caughtS=gag_cfg.caught_s)
        sound = gag_record["sound"]
        print(
            f"Cold open: {gag.gag} 0-{gag_s:.2f}s, caught at {gag_cfg.caught_s:.2f}s, the reel from {gag_s:.2f}s; "
            + (
                f"{sound['action']} {sound['action_db']:+.1f} dB, {sound['scratch']} {sound['scratch_db']:+.1f} dB at "
                f"{sound['scratch_at']:.2f}s"
                if sound
                else "silent (--no-sfx)"
            )
            + f"; audio {gag_mix.name}"
        )
    else:
        for stale in (gag_wav, gag_mix):
            stale.unlink(missing_ok=True)
    (work / "gag.json").write_text(json.dumps(gag_record, ensure_ascii=False, indent=1), encoding="utf-8")

    # 5. Render.
    public_dir = work / "remotion_public"
    stage_public_dir(public_dir, audio, audio_name, background)
    # The files only: the timeline is `images` from 4b, the one the SFX were planned on.
    if manifest is not None:
        copy_screen_images(manifest, public_dir, Library())
    if gag.gag:
        (public_dir / "gags").mkdir()
        for path in gags.files(gag_cfg, gag.gag):
            shutil.copy2(path, public_dir / "gags" / path.name)
    props = {
        "audioSrc": audio_name,
        "mouthCues": cues["mouthCues"],
        "anchors": anchors,
        "mood": mood,
        "loudness": pad_loudness(frame_loudness(wav, FPS), reel_end),
        "captions": captions_before(captions, outro["start"]) if outro else captions,
        "background": background.file,
        "screen": spec,
        "cards": cards,
        "screenImages": images,
        "outro": outro_props(outro, facts, outro_cfg, script),
        "opening": opening_props(script),
        "fx": fx_events,
        "motion": {k: motion[k] for k in MOTION_PROPS},
        "share": share,
        # The cold open (remotion/src/ColdOpen.tsx): the reel above plays from its last frame.
        "gag": gags.props(gag_cfg, gag.gag, face_size(spec)) if gag.gag else None,
    }
    props_json = work / "reel_props.json"
    props_json.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    # One reel renders at a time on the machine (reelline/media/renderlock.py); a second waits here.
    with render_lock():
        versions = render_versions(
            work,
            props,
            props_json,
            public_dir,
            public_dir / audio_name,
            reel_end,
            share_cfg,
            gag_cfg.frames if gag.gag else 0,
        )
        if opts.still_time is not None:
            still = work / "still.png"
            remotion(["still", "Reel", str(still), f"--frame={round(opts.still_time * FPS)}"], props_json, public_dir)
            print(f"Still: {still}")

        # 5b. Cover.
        cover_path: Path | None = work / COVER_FILE
        if opts.no_cover:
            if cover_path.exists():
                cover_path.unlink()
                print(f"Cover: off (--no-cover); removed {cover_path.name}")
            else:
                print("Cover: off (--no-cover)")
            cover_path = None
        else:
            cover_path = render_cover(
                work,
                cover_props(
                    script,
                    headline,
                    anchors,
                    mood,
                    background.file,
                    spec,
                    images[0] if images else None,
                    opts.cover_debug,
                ),
                public_dir,
            )
            print(f"Cover: {cover_path} (TV {images[0]['src'] if images else 'idle'})")

    # 6. Report.
    calls = [u for u in usage if not u.get("pinned")]
    new = [u for u in calls if not u["cached"]]
    total_in = sum(u["input_tokens"] for u in usage)
    total_out = sum(u["output_tokens"] for u in usage)
    print(
        f"\nOutput: {mp4} (mood {mood}, background {background.name}, "
        f"music {track.name if track else 'none'}, "
        f"SFX {f'{len(sfx_plan.events)} events' if sfx_plan else 'off'})"
    )
    rendered = probe_duration(mp4)
    print(
        f"Duration: {rendered:.2f}s ("
        + (f"the cold open {gag_s:.2f}s, then " if gag.gag else "")
        + f"speech {duration:.2f}s"
        + (f", outro {outro['start']:.2f}-{outro['end']:.2f}s of the reel)" if outro else ", no outro)")
    )
    if abs(rendered - (gag_s + reel_end)) > DURATION_TOLERANCE_S:
        raise RenderError(
            f"{mp4} is {rendered:.2f}s, expected {gag_s + reel_end:.2f}s "
            f"(+-{DURATION_TOLERANCE_S}s){' with the outro' if outro else ''}"
            f"{' and the cold open' if gag.gag else ''}"
        )
    tts_cost = cost_usd(total_in, total_out)
    tts_new = cost_usd(sum(u["input_tokens"] for u in new), sum(u["output_tokens"] for u in new))
    print(
        f"TTS: {len(calls)} calls{' + pinned tag' if pinned_tag else ''}, {total_in} input + {total_out} output tokens "
        f"= ${tts_cost:.4f} for the whole reel; this run: {len(new)} calls, ${tts_new:.4f}"
    )
    rows = check_these(script, letters)
    (work / "check_these.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Check these ({len(rows)} words with mean letter score < {LOW_SCORE}; re-roll a paragraph with --regen N):")
    for r in rows:
        print(f"  ¶{r['paragraph']:<3} {r['start']:7.2f}s  {r['score']:.3f}  {r['word']}")

    # 7. Telegram.
    if opts.send:
        try:
            send_reel(mp4)
        except (SendError, ConfigError, NotifyError) as exc:
            raise RenderError(f"--send: {exc}") from None

    images_json, attributions = work / "images.json", work / "attributions.txt"
    return RenderResult(
        name=name,
        work=work,
        mp4=mp4,
        background=background.name,
        mood=mood,
        cover=cover_path,
        duration=rendered,
        images_json=images_json if images_json.is_file() else None,
        attributions=attributions if attributions.is_file() else None,
        check_these=rows,
        tts_cost_usd=tts_cost,
        tts_new_cost_usd=tts_new,
        image_calls=image_calls,
        tts_calls=list(spent["tts"]),
        fx=fx_events,
        fx_left=fx_left,
        versions={p: platform_file(work, p) for p in versions["files"]},
        gag=gag.gag,
        gag_s=gag_s if gag.gag else None,
        gag_mode=gag.mode,
    )


# ---- --background-check -------------------------------------------------------------------


def sample_image(scr: Screen, public_dir: Path, end_s: float) -> dict | None:
    """A screenImages entry for a sample library photo (the first accepted, non-logo
    photo by id that fills this TV), copied into the public dir. None when the library
    has no accepted photo at all (a new channel): the TV shows its idle face."""
    library = Library()
    accepted = sorted(library.all(status="accepted"), key=lambda r: r["id"])
    if not accepted:
        return None
    for record in accepted:
        if record.get("is_logo_or_graphic") or resolution_problem(record["width"], record["height"], scr):
            continue
        src = library.path(record)
        if not src.is_file():
            continue
        crop, focal = cover_crop(record["width"], record["height"], None, scr)
        (public_dir / "screen_images").mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, public_dir / "screen_images" / src.name)
        return {
            "src": f"screen_images/{src.name}",
            "start": 0.0,
            "end": end_s,
            "width": record["width"],
            "height": record["height"],
            "mode": "cover",
            "crop": crop,
            "focal": focal,
            "panelColor": None,
            "seed": f"background-check:{record['id']}",
        }
    raise RenderError(f"No accepted library photo fills this TV ({scr.face_w}x{scr.face_h} face) for the check")


def sample_captions(lines: list[list[str]], end_s: float) -> list[dict]:
    """One two-line caption chunk over the whole silent track, words evenly timed."""
    words = [w for line in lines for w in line]
    step = end_s / len(words)
    timed, i = [], 0
    for line in lines:
        row = []
        for text in line:
            row.append({"text": text, "start": round(i * step, 3), "end": round((i + 1) * step, 3)})
            i += 1
        timed.append(row)
    return [{"chunk_start": 0.0, "chunk_end": end_s, "lines": timed}]


def silent_wav(path: Path, seconds: float, rate: int = 24000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * round(seconds * rate))


def background_check(name: str, send: bool = True) -> tuple[Path, Path, dict]:
    """A still and a cover for background `name` (disabled ones too), no TTS: the TV on
    a sample library photo, the host in DEFAULT_MOOD, a sample two-line caption, the
    cover with a sample headline. Sent to Telegram unless send is False. Returns
    (still, cover, merged screen spec)."""
    check = BACKGROUNDS["check"]
    bg, spec = load_background(name)
    work = CHECK_DIR / bg.name
    work.mkdir(parents=True, exist_ok=True)
    public_dir = work / "remotion_public"
    wav = work / "silence.wav"
    silent_wav(wav, check["audio_s"])
    stage_public_dir(public_dir, wav, wav.name, bg)
    image = sample_image(screen_from_spec(spec), public_dir, check["audio_s"])
    anchors = load_anchors()
    props = {
        "audioSrc": wav.name,
        "mouthCues": [],
        "anchors": anchors,
        "mood": DEFAULT_MOOD,
        "loudness": [0.0] * math.ceil(check["audio_s"] * FPS),
        "captions": sample_captions(check["caption_lines"], check["audio_s"]),
        "background": bg.file,
        "screen": spec,
        "cards": [],
        "screenImages": [image] if image else [],
        "outro": None,
    }
    props_json = work / "reel_props.json"
    props_json.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    still = work / "still.png"
    remotion(["still", "Reel", str(still), f"--frame={round(check['still_s'] * FPS)}"], props_json, public_dir)
    cover = render_cover(
        work, cover_props(None, check["headline"], anchors, DEFAULT_MOOD, bg.file, spec, image, False), public_dir
    )
    if send:
        try:
            state = "enabled" if bg.enabled else "disabled"
            sidecar = f", sidecar {bg.sidecar.name}" if bg.sidecar else ", no sidecar"
            send_photo(still, f"Background check: {bg.name} ({state}{sidecar})")
            send_photo(cover, f"Background check: {bg.name} cover")
        except (SendError, ConfigError, NotifyError) as exc:
            raise RenderError(f"background check send: {exc}") from None
    return still, cover, spec


# ---- CLI ----------------------------------------------------------------------------------


def main() -> None:
    from reelline.pipeline.db import manual_recording, record_manual

    anchors = load_anchors()
    try:
        tracks = {t.name: t for t in load_catalog()}
        names = [b.name for b in all_backgrounds()]
    except (MusicError, BackgroundError) as exc:
        sys.exit(f"FAIL: {exc}")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("script", type=Path, nargs="?", help="UTF-8 script in reelline/media/script_markup.py format")
    parser.add_argument(
        "--background-check",
        metavar="NAME",
        choices=names,
        default=None,
        help="render a still and a cover for this background (no TTS, disabled ones too), "
        "send both to Telegram and print its merged screen geometry; no script",
    )
    parser.add_argument(
        "--mood",
        choices=moods(anchors),
        default=None,
        help=f"rest mouth shown while not speaking (default: the style's, "
        f"{style.current().mood or DEFAULT_MOOD}; "
        "with --cover-only, the reel's)",
    )
    parser.add_argument(
        "--background",
        choices=names,
        default=None,
        help="scene image from assets/backgrounds/ (default: the first enabled one by name; "
        "with --cover-only, the reel's)",
    )
    parser.add_argument("--still-time", type=float, default=None, help="also write a PNG still at this time (s)")
    parser.add_argument(
        "--fresh-tts", action="store_true", help="re-synthesize every paragraph even if a cached clip matches"
    )
    parser.add_argument(
        "--regen",
        type=int,
        action="append",
        default=[],
        metavar="N",
        help="re-synthesize paragraph N (1-based) only; repeatable",
    )
    parser.add_argument(
        "--music",
        choices=["auto", "none", *tracks],
        default="auto",
        help="background track from assets/music/music.json; auto (default) is a weighted pick seeded by the reel name",
    )
    parser.add_argument(
        "--music-level",
        type=float,
        default=MUSIC_LEVEL,
        metavar="LU",
        help=f"music loudness relative to the voice, before ducking (default: {MUSIC_LEVEL:g})",
    )
    parser.add_argument("--no-images", action="store_true", help="no wall-TV images (the TV stays on its idle face)")
    parser.add_argument(
        "--refresh-images", action="store_true", help="re-pick every paragraph's image, ignoring the cached images.json"
    )
    parser.add_argument(
        "--reimage",
        type=int,
        action="append",
        default=[],
        metavar="N",
        help="re-pick paragraph N's image only, excluding earlier picks; repeatable",
    )
    parser.add_argument("--no-outro", action="store_true", help="no outro card: the reel ends with the speech")
    parser.add_argument("--no-sfx", action="store_true", help="no sound effects (removes an old sfx.json)")
    parser.add_argument(
        "--sfx-gain",
        type=float,
        default=0.0,
        metavar="DB",
        help="global SFX level offset in dB, on top of each category's gain (default: 0)",
    )
    cover = parser.add_mutually_exclusive_group()
    cover.add_argument("--no-cover", action="store_true", help=f"no cover image (removes an old {COVER_FILE})")
    cover.add_argument(
        "--cover-only",
        action="store_true",
        help="re-render only the cover from the last render's outputs (no TTS, no reel)",
    )
    parser.add_argument(
        "--cover-debug",
        action="store_true",
        help=f"write {COVER_DEBUG_FILE} instead of {COVER_FILE}, with the safe area drawn",
    )
    parser.add_argument(
        "--send", action="store_true", help="send the finished reel to Telegram (scripts/send_to_phone.py)"
    )
    parser.add_argument(
        "--publish-date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="the deadline fact graphic's days left count from it (default: the date a pipeline "
        "script's file name starts with, else no days-left tag)",
    )
    parser.add_argument(
        "--fx-unchecked", action="store_true", help="show every @fx, even without the fact-check's <script>.fx.json"
    )
    try:
        gag_names = sorted(gags.load().gags)
    except GagError as exc:
        sys.exit(f"FAIL: {exc}")
    parser.add_argument(
        "--gag",
        choices=["auto", "none", *gag_names],
        default="auto",
        help="the cold open: auto (default: remotion/src/gags.json's mode, the A/B by the reel's "
        "name), none, or that gag",
    )
    args = parser.parse_args()

    try:
        if args.background_check:
            if args.script is not None:
                sys.exit("FAIL: --background-check takes no script")
            still, cover_png, spec = background_check(args.background_check)
            print(f"Still: {still}\nCover: {cover_png}\nSent both to Telegram.")
            print(f"Screen geometry for {args.background_check} (screen.json + sidecar):")
            print(json.dumps(spec["screen"], indent=1))
            return
        if args.script is None:
            parser.error("a script is required (or --background-check NAME)")
        opts = RenderOptions(**{k: v for k, v in vars(args).items() if k not in ("script", "background_check")})
        try:
            with manual_recording(args.script.stem):  # each paid call written as it returns
                result = render(args.script, opts)
        except BaseException as exc:
            record_manual(getattr(exc, "paid_calls", []), script_name=args.script.stem)
            raise
        record_manual(result.image_calls + result.tts_calls, script_name=args.script.stem)
    except RenderError as exc:
        sys.exit(f"FAIL: {exc}")


if __name__ == "__main__":
    main()
