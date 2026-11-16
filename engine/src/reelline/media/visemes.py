"""Georgian letter timings -> mouth cues for the renderer.

Input: aligned letters from scripts/align.py, each {char, index, start, end},
where `index` is the letter's position in the script text (consecutive letters of
one word have consecutive indices). Output: Rhubarb-style mouth cues
[{start, end, value}] that are contiguous, snapped to video frames, and cover the
whole audio.

Smoothing:
1. assign_frames: boundaries snap to whole frames. A letter that snaps to zero
   frames competes for the frame nearest its midpoint, and the frame goes to the
   higher-priority shape (SHAPE_PRIORITY). ALWAYS_SHOWN letters (A, G) that lose
   try the next-nearest frames (up to MAX_SHIFT_FRAMES away) and never take the
   last frame of another ALWAYS_SHOWN letter, so every A and G letter is shown.
2. smooth_runs: runs shorter than MIN_FRAMES are fixed up. Consonant shapes
   (MERGE_WHEN_SHORT) merge into their higher-priority neighbour; vowel shapes
   (EXTEND_WHEN_SHORT) take frames from a lower-priority neighbour that can spare
   them, and otherwise stay short. Nothing ever takes frames from A or G.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from reelline import language

MOUTH_CODES = ("X", "A", "B", "C", "D", "E", "F", "G", "H")
SILENCE = "X"

_GROUPS = language.current().mouth_letters
LETTER_TO_MOUTH: dict[str, str] = {letter: code for code, letters in _GROUPS.items() for letter in letters}

# Minimum frames per shape, tunable. See smooth_runs for what happens below the minimum.
MIN_FRAMES = {
    "A": 1,  # closed lips: always shown, even for one frame
    "G": 1,  # the mouth letter "v": always shown, even for one frame
    "F": 2,
    "E": 2,
    "D": 2,
    "H": 2,
    "C": 2,
    "B": 2,
    "X": 1,
}
# Shapes every letter of which must appear on screen, even if moved by a frame or two.
ALWAYS_SHOWN = frozenset("AG")
MAX_SHIFT_FRAMES = 2
# Below their minimum, these shapes are merged away into a neighbour...
MERGE_WHEN_SHORT = frozenset("BCH")
# ...and these grab frames from a lower-priority neighbour, or stay short if none can spare one.
EXTEND_WHEN_SHORT = frozenset("DEF")

# Highest first. Closed lips and lip shapes are what viewers notice; B/C consonants
# look like their neighbours, so they drop first. Silence never takes a frame.
SHAPE_PRIORITY = ("A", "G", "F", "E", "D", "H", "C", "B", "X")
_RANK = {code: rank for rank, code in enumerate(SHAPE_PRIORITY)}

DEFAULT_FPS = 30
# Gaps between words shorter than this keep the previous shape instead of X.
WORD_GAP_HOLD_S = 0.12


def mouth_for(letter: str) -> str:
    try:
        return LETTER_TO_MOUTH[letter]
    except KeyError:
        raise ValueError(f"No mouth shape for {letter!r} (U+{ord(letter):04X})") from None


@dataclass
class Segment:
    start: float  # seconds
    end: float
    code: str
    letter: int | None  # index into the letters list; None for silence
    # Frames (after assign_frames) where this segment's shape is on screen: the
    # frames it owns, or the frame it claimed if a same-shape segment holds it.
    shown_at: list[int] = field(default_factory=list)

    @property
    def shown(self) -> bool:
        return bool(self.shown_at)


def _raw_segments(letters: Sequence[dict[str, Any]], duration: float) -> list[Segment]:
    """One segment per letter plus X for silences, contiguous over [0, duration].

    Within a word each letter holds until the next letter starts. Between words
    the last letter holds through gaps shorter than WORD_GAP_HOLD_S; longer gaps
    are X.
    """
    segments: list[Segment] = []
    cursor = 0.0
    for i, letter in enumerate(letters):
        start = max(letter["start"], cursor)
        if start > cursor:
            segments.append(Segment(cursor, start, SILENCE, None))
        nxt = letters[i + 1] if i + 1 < len(letters) else None
        if nxt is not None and (nxt["index"] == letter["index"] + 1 or nxt["start"] - letter["end"] < WORD_GAP_HOLD_S):
            end = nxt["start"]
        else:
            end = min(letter["end"], duration)
        end = max(end, start)
        segments.append(Segment(start, end, mouth_for(letter["char"]), i))
        cursor = end
    if cursor < duration:
        segments.append(Segment(cursor, duration, SILENCE, None))
    return segments


def assign_frames(
    letters: Sequence[dict[str, Any]], duration: float, fps: int = DEFAULT_FPS
) -> tuple[list[Segment], list[int]]:
    """Give every video frame to one segment.

    Returns the segments (with `shown_at` filled in) and, per frame,
    the index of the segment that owns it. Frame f is on screen for
    [f / fps, (f + 1) / fps).
    """
    if duration <= 0:
        raise ValueError(f"duration must be positive, got {duration}")
    total = math.ceil(duration * fps)
    segments = _raw_segments(letters, duration)
    if not segments:
        segments = [Segment(0.0, duration, SILENCE, None)]

    # 1. Snap boundaries to the nearest frame; non-empty segments own their frames.
    owner = [-1] * total
    contenders = []
    for i, seg in enumerate(segments):
        f0 = min(round(seg.start * fps), total)
        f1 = total if i == len(segments) - 1 else min(round(seg.end * fps), total)
        if f1 > f0:
            owner[f0:f1] = [i] * (f1 - f0)
        elif seg.code != SILENCE:
            contenders.append(i)

    # 2. Zero-frame letters, highest priority first, claim the frame nearest their
    #    midpoint if they outrank its current owner. ALWAYS_SHOWN letters also try
    #    nearby frames, and never take another ALWAYS_SHOWN letter's last frame.
    contenders.sort(key=lambda i: (_RANK[segments[i].code], segments[i].start))
    counts = Counter(owner)
    claimed: dict[int, int] = {}
    for i in contenders:
        seg = segments[i]
        nearest = min(max(math.floor((seg.start + seg.end) / 2 * fps), 0), total - 1)
        candidates = [nearest]
        if seg.code in ALWAYS_SHOWN:
            # Shift past whoever holds the nearest frame, keeping spoken order.
            step = 1 if seg.start >= segments[owner[nearest]].start else -1
            for k in range(1, MAX_SHIFT_FRAMES + 1):
                candidates += [nearest + step * k, nearest - step * k]
            candidates = [f for f in candidates if 0 <= f < total]
        claimed[i] = nearest
        for f in candidates:
            other = segments[owner[f]]
            if other.code == seg.code:
                claimed[i] = f
                break
            if _RANK[seg.code] >= _RANK[other.code]:
                continue
            if other.code in ALWAYS_SHOWN and counts[owner[f]] == 1:
                continue
            counts[owner[f]] -= 1
            counts[i] += 1
            owner[f] = i
            claimed[i] = f
            break

    for f, i in enumerate(owner):
        segments[i].shown_at.append(f)
    for i, f in claimed.items():
        if not segments[i].shown_at and segments[owner[f]].code == segments[i].code:
            segments[i].shown_at.append(f)
    return segments, owner


Run = list  # [code, frames]


def _coalesce(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for code, n in runs:
        if n <= 0:
            continue
        if out and out[-1][0] == code:
            out[-1][1] += n
        else:
            out.append([code, n])
    return out


def _merge_short(runs: list[Run]) -> list[Run]:
    """Merge MERGE_WHEN_SHORT runs below their minimum into a neighbour.

    Shortest (then lowest-priority) first; each goes to the higher-priority
    neighbour, or the longer one on a tie.
    """
    runs = _coalesce(runs)
    while len(runs) > 1:
        short = [i for i, (code, n) in enumerate(runs) if code in MERGE_WHEN_SHORT and n < MIN_FRAMES[code]]
        if not short:
            break
        i = min(short, key=lambda k: (runs[k][1], -_RANK[runs[k][0]], k))
        neighbours = [j for j in (i - 1, i + 1) if 0 <= j < len(runs)]
        j = min(neighbours, key=lambda k: (_RANK[runs[k][0]], -runs[k][1]))
        runs[j][1] += runs[i][1]
        runs[i][1] = 0
        runs = _coalesce(runs)
    return runs


def _extend_short(runs: list[Run]) -> list[Run]:
    """Grow EXTEND_WHEN_SHORT runs below their minimum by moving a boundary.

    A frame is only taken from a lower-priority neighbour that stays at or above
    its own minimum (the lowest-priority, then longest, donor first).
    """
    for i, run in enumerate(runs):
        code = run[0]
        if code not in EXTEND_WHEN_SHORT:
            continue
        while run[1] < MIN_FRAMES[code]:
            donors = [
                j
                for j in (i - 1, i + 1)
                if 0 <= j < len(runs) and _RANK[runs[j][0]] > _RANK[code] and runs[j][1] > MIN_FRAMES[runs[j][0]]
            ]
            if not donors:
                break
            j = max(donors, key=lambda k: (_RANK[runs[k][0]], runs[k][1]))
            runs[j][1] -= 1
            run[1] += 1
    return runs


def smooth_runs(runs: list[Run]) -> list[Run]:
    """Apply the per-shape minimums in MIN_FRAMES to runs of [code, frames]."""
    return _coalesce(_extend_short(_merge_short(runs)))


def frame_codes(letters: Sequence[dict[str, Any]], duration: float, fps: int = DEFAULT_FPS) -> list[str]:
    """Final mouth shape for every video frame, after both smoothing steps."""
    segments, owner = assign_frames(letters, duration, fps)
    runs = smooth_runs([[segments[i].code, 1] for i in owner])
    return [code for code, n in runs for _ in range(n)]


def letters_to_cues(letters: Sequence[dict[str, Any]], duration: float, fps: int = DEFAULT_FPS) -> list[dict[str, Any]]:
    """Build contiguous mouth cues covering [0, ceil(duration * fps) / fps].

    Cue boundaries are exact frame times (frame / fps), so each cue holds a whole
    number of frames, and consecutive cues always differ in shape.
    """
    cues: list[dict[str, Any]] = []
    for f, code in enumerate(frame_codes(letters, duration, fps)):
        if cues and cues[-1]["value"] == code:
            cues[-1]["end"] = (f + 1) / fps
        else:
            cues.append({"start": f / fps, "end": (f + 1) / fps, "value": code})
    return cues
