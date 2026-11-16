"""The motion pass: the camera's framings and angles, push-ins on punchlines, his reactions,
lean-ins, bounces, eye-stalk moves and glances up at the TV (channel docs/fx.md "Motion"). render_reel.py plans
it here, Remotion draws it (remotion/src/motion.ts and camera.ts, the `motion` prop); the tunables are
remotion/src/motion.json.

Submodules: spec (motion.json, checked), markup (punchlines and other signals from the script's markup),
framing (shot geometry and its checks), plan (push-ins, reactions, glances, bounces, stalks, leans),
angles (front / 3/4 / behind), shots (wide / medium / close), beats (visual changes, gap filling, log).

The punchline signal is the script's markup: the sentence right before a laugh tag (punchline.tags) and
the closing jab's last sentence. A punchline lands on its last word's end. The flirt and details lines
never have one; the share line has one like any paragraph.

plan() gives at most punchline.maxPerReel a push-in (closing jab first, minGapS apart), never while a
fact graphic is out; a push-in or reaction may run on under the end card, the closing jab keeps its
push-in. Every punchline gets a reaction pose and a bounce (also each laugh tag elsewhere, the share
line's end). Each TV picture change after the first and each fact graphic's pop-out gets a glance up at
the TV, except during a reaction. A paragraph's @style (else the reel's mood) gives its first word an
eye-stalk reaction; emphasis a lean-in. Random picks are seeded: the same script and timing always plan
the same.

The camera: the reel opens wide, then cuts every seeded 4-6 s between wide, medium and close shots, on a
sentence or clause boundary only (cut_points()), never during a card's flight or a push-in. A close
shot never shows while a card is out; while the share arrow is up only framing.arrowShots.
framing_problems() checks each shot keeps the mascot's eyes and mouth whole, below the captions and above the
platform's bottom bar.

The camera's angle (framing.angles; plan_angles(), before the framings): front is the majority; another
angle (reelline/media/angles.py) is one shot from a sentence's end to a sentence's end, never over a fact
card or the share arrow, nor, without his face, over a push-in or a reaction. 3/4 (kind share) takes a
seeded share of the body; back (kind beat) short beats where a new TV picture comes up.

Then the beats (motion.json beats): beats() lists the visual changes and fill_beats() fills each stretch
longer than maxGapS with a push-in landing on a word's end or a glance up at the TV (why "beat").

joke_paragraphs() is the same punchline signal per paragraph, plus the share line's, for the fact graphics
(reelline/media/fx.py planned()).

The style (reelline/media/style.py): motion.json is the classic style's; the straight style lays
Style.motion over it: no closing-jab punchline and no bounce on the share line. Its prompt allows no
laugh tags, so it has no punchlines.
"""

from __future__ import annotations

from reelline.media.motion.angles import beat_glances, plan_angles
from reelline.media.motion.base import INF, MOTION_JSON, SCREEN_JSON, MotionError
from reelline.media.motion.beats import angle_shares, angle_summary, beats, fill_beats, largest_gap, summary
from reelline.media.motion.framing import check_framing, face_boxes, framing_problems, max_push, on_screen
from reelline.media.motion.markup import (
    bounce_moments,
    cut_points,
    emphasis_words,
    joke_paragraphs,
    punchline_tokens,
    punchlines,
    stalk_pose,
)
from reelline.media.motion.plan import plan, plan_bounces, plan_leans, plan_stalks
from reelline.media.motion.shots import plan_shots
from reelline.media.motion.spec import ANGLE_KINDS, config

__all__ = [
    "ANGLE_KINDS",
    "INF",
    "MOTION_JSON",
    "SCREEN_JSON",
    "MotionError",
    "angle_shares",
    "angle_summary",
    "beat_glances",
    "beats",
    "bounce_moments",
    "check_framing",
    "config",
    "cut_points",
    "emphasis_words",
    "face_boxes",
    "fill_beats",
    "framing_problems",
    "joke_paragraphs",
    "largest_gap",
    "max_push",
    "on_screen",
    "plan",
    "plan_angles",
    "plan_bounces",
    "plan_leans",
    "plan_shots",
    "plan_stalks",
    "punchline_tokens",
    "punchlines",
    "stalk_pose",
    "summary",
]
