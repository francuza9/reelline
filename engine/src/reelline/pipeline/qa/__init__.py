"""QA: automatic checks on a rendered reel, between the render and the review.

Checks (every threshold in QA below; every time is the reel's own: a cold open before it,
reel_props.json gag, reelline/media/gags.py, is skipped: gag_offset()):
  technical (fail)  duration (the reel's, without its cold open), 1080x1920 at 30 fps H.264 with an audio stream, the file
                    at most publish.max_mb (the publish guard's limit; fail), the
                    video stream's pixel format and color tags (yuv420p, BT.709, TV
                    range: remotion/encode.json's) and its bitrate at least encode.json
                    qa.minVideoMbps (warn),
                    integrated loudness near QA["loudness_lufs"] (warn / fail outside
                    the two tolerances), the true peak at most QA["true_peak_warn_db"]
                    (warn; above QA["true_peak_fail_db"] it clips: fail), no silence
                    longer than QA["silence_max_s"] in the voice-only track
                    (out/reels/<name>.wav: the voice section, the outro excluded),
                    cover.png at 1080x1920 whose safe-area check passed
  end card          the outro's end card (reel_props.json outro): its facts within the
                    card's limits, no markup (reelline/media/outro.py card_problems()): fail; no
                    facts: fail when the script points to the card (a details line),
                    else warn; the card comes in over the last line and holds outro.json
                    holdS after the voice track ends, music only (QA["card_voice_tolerance_s"]
                    short of it at most): fail
  versions          each platform's file (reel_<platform>.mp4, reelline/media/share.py; the share
                    arrow per platform): there, as long as reel.mp4 (one frame at most),
                    1080x1920 at 30 fps H.264 with an audio stream, the video format of
                    reel.mp4's: fail; its loudness and true peak, when they aren't reel.mp4's
                    (they are by design: one audio track; reel.mp4's own are the technical
                    check's), within the technical limits: fail (loudness off by more than
                    loudness_warn_lu: warn); a render from before the versions (no
                    versions.json): warn
  fx (fail)         the fact graphics (reel_props.json fx; the value lists them): the
                    settled card inside fx.json placements.popout.safe (below the top
                    bar, inside the side margins, above a one-line caption) and above
                    the mascot's eyes (reelline/media/fx.py safe_problems(), eye_tops()); no caption
                    chunk (captions.json) of more than one line while a card is out; no
                    two cards overlapping, none still out when the end card opens;
                    paragraph 1's card the frame-0 card (out from 0, holding at least
                    fx.json opening.minHoldS), no other card one. Remotion checks the
                    card's box at render time too (remotion/src/Fx.tsx), so a render
                    that got here passed it.
  audio (warn)      words the aligner scored below QA["low_score"] (check_these.json)
  opening (warn)    the voice-only track's first sound (an opening emotion's vocal tag
                    included) within QA["voice_start_max_s"]; with an opening emotion
                    (reel_props.json opening.emotion) the first word (captions.json)
                    starts by QA["emotional_first_word_max_s"]. The first sentence's length
                    isn't checked: it's a full sentence, not a title (prompts/story.md).
                    The beats (reel_props.json's pictures, cards and motion; reelline/media/motion.py
                    beats()): no stretch of the first motion.json beats.windowS seconds
                    longer than beats.maxGapS + QA["beat_gap_tolerance_s"] without a
                    visual change (props without a motion pass: not checked)
  motion            the camera (reel_props.json motion.shots; props without shots: not
                    checked): every shot used inside motion.json framing.safe with this
                    reel's anchors and TV (reelline/media/motion.py framing_problems(): the mascot's
                    eyes and mouth whole, below the captions' bottom, above the bottom bar,
                    below the card; the TV whole in a shot that isn't close; in another
                    camera angle its rig's face and TV, reelline/media/angles.py), from 0, no
                    shot twice in a row (the same framing in the same angle), no close
                    shot (or one not in cardShots) while a card is out or a push-in runs,
                    no angle outside framing.angles cardAngles while a card is out nor
                    outside arrowAngles while the share arrow is up, a known angle, no
                    cut inside a word (captions.json): fail. The beats over the whole reel (to the end card): no stretch
                    longer than beats.body.maxGapS + QA["beat_gap_tolerance_s"] without a
                    visual change: warn
  text              no leftover markup in the displayed captions (captions.json) or
                    the post caption (its URLs skipped): fail; the last caption chunk(s),
                    whole chunks from the end covering the tag's words, read exactly
                    TAG_LINE (tag_caption_problems()): fail; the source URL is
                    reachable (HEAD, then GET; one retry): warn; the caption fields
                    within the strictest the channel config steps.caption.limits entry
                    that applies to each: fail; the image credits line
                    (reelline/pipeline/credits.py caption_problems()): there, on caption line
                    publish.credits_page.max_line or above, when the reel shows any image
                    (images.json), absent when it shows none: fail
  visual (fail)     QA["frames_at"] frames of the voice section, each while a caption
                    is on screen, through Gemini vision (QA_SCHEMA): the mascot visible,
                    captions readable and clear of his eyes, nothing broken or black.
                    Results cached by frame hash in QA["vision_cache"].

The cover's safe area (remotion/src/Cover.tsx) is checked inside the cover render,
which fails when it's off, so nothing on disk records it: a cover this render wrote
(Subject.cover_fresh) passed; otherwise one newer than cover_props.json did (the
still is only written when the render succeeds).

run_flow() is produce's QA (reelline/pipeline/produce.py): first one TTS re-roll round for the
paragraphs with low-score words (render_reel.py --regen; never the tag paragraph,
pinned to voice/tag.wav). The old takes are kept until the new ones are aligned; per
paragraph the take with fewer words below QA["low_score"] stays (tie: the higher
minimum word score), and if any old take stays the reel is rendered again from the
cache. Then the checks; on a failure one full re-render (same script, images and TTS
takes) and the checks again. The result goes to the review either way: QA ✅, or QA ❌
with the reasons. qa_<reel id>.json in the reel dir has every check (value, threshold, result)
and the re-roll and re-render history.

CLI: python -m reelline.pipeline.qa run <reel_dir> runs the checks on an existing render (no
re-roll, no re-render) and prints the report; the vision calls' cost is recorded,
tagged manual, with the reel's script and latest reel when there are. Credentials (GEMINI_API_KEY) from .env.
"""

from __future__ import annotations

from reelline.pipeline.qa.base import (
    COLOR_TAGS,
    ENCODE,
    FRAME_FAILS,
    QA,
    QA_PROMPT,
    QA_SCHEMA,
    Check,
    QaError,
    Report,
    Subject,
    subject_from_dir,
)
from reelline.pipeline.qa.cli import cmd_run, main
from reelline.pipeline.qa.graphics import card_checks, fx_checks, outro_props, version_checks
from reelline.pipeline.qa.media import VIDEO_TAGS, audio_levels, gag_offset, loudness, probe, silences
from reelline.pipeline.qa.motion import framing_problems, motion_checks
from reelline.pipeline.qa.opening import (
    audio_checks,
    beat_check,
    low_words,
    motion_beats,
    opening_checks,
    opening_emotion,
    voice_onset,
    word_line,
)
from reelline.pipeline.qa.report import (
    MARK,
    caption_fields,
    qa_json,
    record_calls,
    report_text,
    script_for_reel,
    summary_lines,
    write_qa_json,
)
from reelline.pipeline.qa.rerolls import (
    Outcome,
    better_take,
    reroll,
    reroll_takes,
    run_flow,
    take_cost,
    take_files,
    take_score,
)
from reelline.pipeline.qa.technical import cover_check, technical_checks
from reelline.pipeline.qa.text import (
    caption_checks,
    caption_chunks,
    caption_words,
    credits_check,
    limit_problems,
    markup_found,
    recheck_caption,
    tag_caption_problems,
    tag_captions,
    text_checks,
    url_status,
)
from reelline.pipeline.qa.vision import (
    Checker,
    VisionCache,
    ask_vision,
    extract_frame,
    frame_times,
    gemini_client,
    vision_jpeg,
    visual_checks,
    voice_end,
)

__all__ = [
    "COLOR_TAGS",
    "ENCODE",
    "FRAME_FAILS",
    "MARK",
    "QA",
    "QA_PROMPT",
    "QA_SCHEMA",
    "VIDEO_TAGS",
    "Check",
    "Checker",
    "Outcome",
    "QaError",
    "Report",
    "Subject",
    "VisionCache",
    "ask_vision",
    "audio_checks",
    "audio_levels",
    "beat_check",
    "better_take",
    "caption_checks",
    "caption_chunks",
    "caption_fields",
    "caption_words",
    "card_checks",
    "cmd_run",
    "cover_check",
    "credits_check",
    "extract_frame",
    "frame_times",
    "framing_problems",
    "fx_checks",
    "gag_offset",
    "gemini_client",
    "limit_problems",
    "loudness",
    "low_words",
    "main",
    "markup_found",
    "motion_beats",
    "motion_checks",
    "opening_checks",
    "opening_emotion",
    "outro_props",
    "probe",
    "qa_json",
    "recheck_caption",
    "record_calls",
    "report_text",
    "reroll",
    "reroll_takes",
    "run_flow",
    "script_for_reel",
    "silences",
    "subject_from_dir",
    "summary_lines",
    "tag_caption_problems",
    "tag_captions",
    "take_cost",
    "take_files",
    "take_score",
    "technical_checks",
    "text_checks",
    "url_status",
    "version_checks",
    "vision_jpeg",
    "visual_checks",
    "voice_end",
    "voice_onset",
    "word_line",
    "write_qa_json",
]
