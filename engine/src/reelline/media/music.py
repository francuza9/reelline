"""Background music: pick a bed from assets/music/music.json and mix it under the voice.

load_catalog() reads the track list ({file, group, weight} per track; the weights
are relative and only matter against each other). pick_track() makes a weighted
pick seeded by the reel name, so re-renders keep their track. mix() lays the track
under the voice track: seeded start offset, looped with a crossfade if needed, at a
level relative to the voice (the caller's knob, render_reel.py MUSIC_LEVEL), ducked
further while the voice is speaking, faded in and out, and the whole mix
loudness-normalized. Ducking, fades and the mix target are in the config block below.
The mix can run past the voice (the outro's end card, reelline/media/outro.py): the voice is
padded with silence to `length_s` and the music plays on under it, raised by a MusicLift
from the card's start (ramped, so the card's music is louder than the reel's) and fading
out over `fade_out_s`; the report gives the music's loudness on the card (before the fade).
Sound effects (the channel's sfx, SfxHit) go into the same mix at their times and gains
relative to the voice: they aren't ducked and don't duck the music, and none peaks
above the voice's sample peak plus PEAK_CEILING_DB (reelline/media/sfx/config.py). The final
normalization gain is measured on the mix without them, so it's the same as with no
SFX, and a result peaking above MIX_TP_LIMIT_DB fails. With no track, the mix is the
voice and the SFX, normalized the same way.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.media.sfx.config import MIX_TP_LIMIT_DB, PEAK_CEILING_DB

MUSIC_DIR = conf().channel.dir / "assets" / "music"
CATALOG = MUSIC_DIR / "music.json"

# ---- Mix config ------------------------------------------------------------
# Extra ducking while he speaks: sidechaincompress on the music, keyed on the voice
# (threshold is linear on the key's level; attack/release in ms). The voice sits
# around -16 LUFS, so this takes about another 4 dB off the music under speech.
# The slow release holds the duck through word and paragraph gaps instead of pumping.
DUCK = {"threshold": 0.055, "ratio": 1.6, "attack": 120, "release": 1200, "knee": 4.0, "makeup": 1.0}
FADE_IN_S = 0.5
FADE_OUT_S = 1.0
# Crossfade where the track loops back to its start (only when the reel outlasts
# the rest of the track from the offset).
LOOP_CROSSFADE_S = 3.0
# Silence below this at a track's end isn't played (the loop crossfades before it).
TAIL_SILENCE_DB = -45
# Final mix target (two-pass loudnorm) and format.
MIX_LOUDNORM = {"I": -14.0, "TP": -1.5, "LRA": 11.0}
MIX_RATE = 48000
# ----------------------------------------------------------------------------


class MusicError(ReellineError, RuntimeError):
    """Raised for a bad music catalog or a failed ffmpeg step."""


@dataclass(frozen=True)
class Track:
    name: str  # file stem, what --music takes
    path: Path
    group: str
    weight: float


@dataclass(frozen=True)
class MusicLift:
    """The bed raised by `db` from `start_s` on (a linear gain ramp over `ramp_s`): the end
    card's music (remotion/src/outro.json musicLiftDb, musicLiftS)."""

    start_s: float
    db: float
    ramp_s: float

    def volume(self) -> str:
        """An ffmpeg volume expression (eval=frame) for the lift, t from the reel's start."""
        k = 10 ** (self.db / 20)
        return f"volume='1+{k - 1:.6f}*min(max((t-{self.start_s:.3f})/{self.ramp_s:.3f},0),1)':eval=frame"


@dataclass(frozen=True)
class SfxHit:
    """A sound effect for mix(): `path` starting at `time` s, `gain_db` relative to the
    voice in the sound's own measure: with `by_peak`, its sample peak against the
    voice's sample peak, else its loudness (LUFS) against the voice's. `level_db` is
    the sound's own level in that measure, `peak_db` its sample peak."""

    label: str
    path: Path
    time: float
    gain_db: float
    level_db: float
    peak_db: float
    by_peak: bool


@dataclass(frozen=True)
class SfxApplied:
    label: str
    time: float
    volume_db: float  # applied to the file in the premix
    capped: bool  # turned down to the peak ceiling


@dataclass(frozen=True)
class MixReport:
    offset: float | None  # s into the track where the reel starts (None: no track)
    loops: int  # times the track wrapped back to its start
    gain_db: float | None  # applied to the track before ducking
    duck_db: float | None  # how much the ducking took off the music's integrated loudness
    voice_lufs: float  # in the final mix
    music_lufs: float | None  # in the final mix (after ducking)
    out_lufs: float
    out_tp: float
    normalization: str  # loudnorm's mode: "linear", or "dynamic" if linear would exceed TP
    sfx: tuple[SfxApplied, ...] = ()
    sfx_tp: float | None = None  # true peak of the SFX alone, at the final mix's gain
    # The music's loudness in the final mix from `end_from` to the start of its fade-out
    # (the end card's music, steady), None without a track or an `end_from`.
    music_end_lufs: float | None = None


def load_catalog(path: Path = CATALOG) -> list[Track]:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MusicError(f"Can't read {path}: {exc}") from exc
    tracks = []
    for entry in spec:
        track = Track(Path(entry["file"]).stem, path.parent / entry["file"], entry["group"], float(entry["weight"]))
        if not track.path.is_file():
            raise MusicError(f"{path.name}: {track.path} doesn't exist")
        if track.weight < 0:
            raise MusicError(f"{path.name}: {track.name} has a negative weight")
        tracks.append(track)
    names = [t.name for t in tracks]
    if len(set(names)) != len(names):
        raise MusicError(f"{path.name}: duplicate track names")
    return tracks


def pick_track(tracks: list[Track], seed: str) -> Track:
    """Weighted pick, deterministic for a given seed.

    Each track draws its own seeded key u in [0, 1) and the largest u ** (1 / weight)
    wins (Efraimidis-Spirakis), which picks each track with probability proportional
    to its weight. A track's key doesn't depend on the others, so changing one
    track's weight only moves the reels that pick or would now pick that track.
    """
    live = [t for t in tracks if t.weight > 0]
    if not live:
        raise MusicError("Every music track has weight 0; use --music none or a track name")
    return max(live, key=lambda t: random.Random(f"{seed}/{t.name}").random() ** (1 / t.weight))


def _ffmpeg(args: list[str]) -> str:
    if shutil.which("ffmpeg") is None:
        raise MusicError("ffmpeg not found on PATH; needed to mix music")
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise MusicError(f"ffmpeg failed:\n{result.stderr}")
    return result.stderr


def _duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MusicError(f"ffprobe failed on {path}:\n{result.stderr}")
    return float(result.stdout.strip())


EBUR128 = "ebur128=framelog=quiet:peak=true"
# The voice as it enters the mix (ffmpeg's mono -> stereo upmix is -3 dB per channel,
# so it measures the same as the mono file).
VOICE_CHAIN = f"aresample={MIX_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"


def voice_chain(length_s: float) -> str:
    """VOICE_CHAIN, then silence up to `length_s` (the outro runs past the voice)."""
    return f"{VOICE_CHAIN},apad=whole_dur={length_s:.3f}"


def _sample_peak(log: str, what: str) -> float:
    """Sample peak (dBFS) from an ebur128 summary with peak=sample."""
    peak = re.findall(r"Sample peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS", log)
    if not peak:
        raise MusicError(f"No ebur128 sample peak in ffmpeg output for {what}:\n{log[-2000:]}")
    return float(peak[-1])


def _ebur128_summary(log: str, what: str) -> dict:
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", log)
    if not lufs or not peak:
        raise MusicError(f"No ebur128 summary in ffmpeg output for {what}:\n{log[-2000:]}")
    return {"lufs": float(lufs[-1]), "tp": float(peak[-1])}


def measure(path: Path, silence: bool = False, window: tuple[float, float] | None = None) -> dict:
    """{"lufs", "tp"} of the file (EBU R128 integrated loudness, true peak dBTP), of
    [start, end) s only with `window`; with `silence`, also "end": where trailing silence
    below TAIL_SILENCE_DB starts (the duration if there's none)."""
    af = EBUR128
    if silence:
        af = f"silencedetect=n={TAIL_SILENCE_DB}dB:d=0.3,{af}"
    cut = ["-ss", f"{window[0]:.3f}", "-to", f"{window[1]:.3f}"] if window else []
    log = _ffmpeg([*cut, "-i", str(path), "-af", af, "-f", "null", "-"])
    out = _ebur128_summary(log, str(path))
    if silence:
        duration = _duration(path)
        starts = re.findall(r"silence_start: (-?[\d.]+)", log)
        ends = re.findall(r"silence_end: ([\d.]+)", log)
        # silencedetect closes a trailing silence at EOF, so check it reaches the end.
        trailing = starts and (len(ends) < len(starts) or float(ends[-1]) >= duration - 0.1)
        out["end"] = max(0.0, float(starts[-1])) if trailing else duration
    return out


def choose_offset(content_end: float, duration: float, seed: str) -> float:
    """Seeded start offset: anywhere the reel still fits before `content_end`, or,
    if the whole track is shorter than the reel, somewhere in its first half."""
    rng = random.Random(f"{seed}/offset")
    room = content_end - duration
    return round(rng.uniform(0, room) if room > 0 else rng.uniform(0, content_end / 2), 3)


def bed_filter(
    offset: float,
    content_end: float,
    duration: float,
    gain_db: float,
    fade_out_s: float = FADE_OUT_S,
    lift: MusicLift | None = None,
) -> tuple[str, int]:
    """Filtergraph taking the track to [bed]: `duration` s from `offset`, wrapping to
    0:00 with LOOP_CROSSFADE_S crossfades as often as needed, at `gain_db` (raised by
    `lift` from its start), faded in over FADE_IN_S and out over the last `fade_out_s`.
    Returns (graph, number of loops).

    The graph reads the track once per segment, from inputs 1..loops + 1: fed from
    one input through asplit, acrossfade drops the later segments.
    """
    xf = LOOP_CROSSFADE_S
    if content_end <= 2 * xf:
        raise MusicError(f"Track is too short to loop ({content_end:.1f}s)")
    segments = [(offset, content_end)]
    # Each wrap adds the full track minus the crossfade overlap.
    while content_end - offset + (len(segments) - 1) * (content_end - xf) < duration:
        segments.append((0.0, content_end))
    n = len(segments)
    parts = [f"[{k + 1}:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[m{k}]" for k, (a, b) in enumerate(segments)]
    prev = "m0"
    for k in range(1, n):
        parts.append(f"[{prev}][m{k}]acrossfade=d={xf}:c1=qsin:c2=qsin[x{k}]")
        prev = f"x{k}"
    parts.append(
        f"[{prev}]atrim=end={duration},asetpts=PTS-STARTPTS,volume={gain_db:.2f}dB,"
        + (f"{lift.volume()}," if lift else "")
        + f"afade=t=in:d={FADE_IN_S},afade=t=out:st={max(0.0, duration - fade_out_s):.3f}:d={fade_out_s},"
        f"aresample={MIX_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[bed]"
    )
    return ";".join(parts), n - 1


def sfx_filter(
    hits: list[SfxHit],
    first_input: int,
    duration: float,
    voice_lufs: float,
    voice_peak: float,
    ceiling_db: float = PEAK_CEILING_DB,
) -> tuple[str, list[SfxApplied]]:
    """Filtergraph taking hits[k] from input first_input + k to [sfx_mix] and [sfx_dry]
    (stereo MIX_RATE, `duration` long), and what was applied to each. A hit's volume
    puts it `gain_db` from the voice in its measure, but never above the voice's
    sample peak plus `ceiling_db`."""
    parts, labels, applied = [], [], []
    for k, hit in enumerate(hits):
        volume = (voice_peak if hit.by_peak else voice_lufs) + hit.gain_db - hit.level_db
        ceiling = voice_peak + ceiling_db - hit.peak_db
        applied.append(SfxApplied(hit.label, hit.time, round(min(volume, ceiling), 2), volume > ceiling))
        parts.append(
            f"[{first_input + k}:a]aresample={MIX_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={min(volume, ceiling):.2f}dB,adelay=delays={round(hit.time * MIX_RATE)}S:all=1[s{k}]"
        )
        labels.append(f"[s{k}]")
    parts.append(
        f"{''.join(labels)}amix=inputs={len(hits)}:duration=longest:normalize=0,"
        f"apad=whole_dur={duration:.3f},atrim=end={duration:.3f},asplit=2[sfx_mix][sfx_dry]"
    )
    return ";".join(parts), applied


def mix(
    voice: Path,
    track: Track | None,
    out: Path,
    seed: str,
    level_lu: float,
    length_s: float | None = None,
    fade_out_s: float = FADE_OUT_S,
    sfx: list[SfxHit] = (),
    lift: MusicLift | None = None,
    end_from: float | None = None,
) -> MixReport:
    """Write `out` (stereo MIX_RATE 16-bit WAV, `length_s` long, default the voice's
    length): the voice with `track` under it at `level_lu` LU relative to the voice
    (before ducking; raised by `lift` from its start), the music fading out over the last
    `fade_out_s`, and the `sfx` hits (sfx_filter), normalized to MIX_LOUDNORM. Past the
    voice's end the music plays on and the ducking lets go. `track` None: the voice and the
    SFX only. The report's music_end_lufs is the music from `end_from` (default: the
    lift's start) to its fade-out."""
    voice_s = _duration(voice)
    duration = voice_s if length_s is None else length_s
    if duration < voice_s - 1e-3:
        raise MusicError(f"Mix length {duration:.3f}s is shorter than the voice ({voice_s:.3f}s)")
    if track is None and not sfx:
        raise MusicError("Nothing to mix under the voice: no track and no SFX")
    if lift is not None and not 0 <= lift.start_s < duration:
        raise MusicError(f"The music lift starts at {lift.start_s:.3f}s, outside the mix (0-{duration:.3f}s)")
    end_from = lift.start_s if end_from is None and lift is not None else end_from
    late = [h.label for h in sfx if not 0 <= h.time < duration]
    if late:
        raise MusicError(f"SFX outside the mix (0-{duration:.3f}s): {', '.join(late)}")
    # Both sides as they enter the mix: the upmixed voice, and the stretch of the
    # track that's actually played (a track's loudness varies along it) at unity
    # gain with the loops and fades.
    probes = {"voice": f"[0:a]{voice_chain(duration)}"}
    tracks: list[str] = []
    if track is not None:
        m = measure(track.path, silence=True)
        offset = choose_offset(m["end"], duration, seed)
        probe, loops = bed_filter(offset, m["end"], duration, 0.0, fade_out_s)
        tracks = ["-i", str(track.path)] * (loops + 1)
        probes["bed"] = f"{probe};[bed]anull"
    levels, voice_peak = {}, 0.0
    for name, graph in probes.items():
        log = _ffmpeg(
            [
                "-i",
                str(voice),
                *tracks,
                "-filter_complex",
                f"{graph},{EBUR128}+sample[probe]",
                "-map",
                "[probe]",
                "-f",
                "null",
                "-",
            ]
        )
        levels[name] = _ebur128_summary(log, f"{name} of {voice.name}")["lufs"]
        if name == "voice":
            voice_peak = _sample_peak(log, f"voice of {voice.name}")

    # [nosfx]: the mix without the SFX, which the normalization is measured on (with
    # no track, that's the voice alone: voice_dry).
    def split(label, names):
        return f"{label}asplit={len(names)}{''.join(f'[{n}]' for n in names)}"

    voice_split = ["voice", "voice_dry"] + (["key"] if track else []) + (["voice_ns"] if track and sfx else [])
    mixed = ["[voice]"]
    graph = [split(f"[0:a]{voice_chain(duration)},", voice_split)]
    if track is not None:
        gain_db = levels["voice"] + level_lu - levels["bed"]
        bed, _ = bed_filter(offset, m["end"], duration, gain_db, fade_out_s, lift)
        duck = ":".join(f"{k}={v_}" for k, v_ in DUCK.items())
        graph += [
            f"{bed};[bed]asplit=2[bed_mix][bed_dry]",
            split(
                f"[bed_mix][key]sidechaincompress={duck},", ["ducked", "ducked_dry"] + (["ducked_ns"] if sfx else [])
            ),
        ]
        if sfx:
            graph.append("[voice_ns][ducked_ns]amix=inputs=2:duration=first:normalize=0[nosfx]")
        mixed.append("[ducked]")
    applied: list[SfxApplied] = []
    if sfx:
        sfx_graph, applied = sfx_filter(list(sfx), 1 + len(tracks) // 2, duration, levels["voice"], voice_peak)
        graph.append(sfx_graph)
        mixed.append("[sfx_mix]")
    sfx_inputs = [a for hit in sfx for a in ("-i", str(hit.path))]
    graph.append(f"{''.join(mixed)}amix=inputs={len(mixed)}:duration=first:normalize=0[mix]")
    stem_names = (
        ["mix", "voice_dry"]
        + (["bed_dry", "ducked_dry"] if track else [])
        + (["sfx_dry"] if sfx else [])
        + (["nosfx"] if track and sfx else [])
    )
    target = ":".join(f"{k}={v_}" for k, v_ in MIX_LOUDNORM.items())
    with tempfile.TemporaryDirectory() as tmp:
        stems = {name: Path(tmp) / f"{name}.wav" for name in stem_names}
        outputs = [a for name, path in stems.items() for a in ("-map", f"[{name}]", "-c:a", "pcm_f32le", str(path))]
        _ffmpeg(["-y", "-i", str(voice), *tracks, *sfx_inputs, "-filter_complex", ";".join(graph), *outputs])
        premix = stems["mix"]
        basis = premix if not sfx else stems["nosfx"] if track else stems["voice_dry"]
        log = _ffmpeg(["-i", str(basis), "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"])
        blocks = re.findall(r"\{[^{}]*\}", log)
        if not blocks:
            raise MusicError(f"No loudnorm measurement in ffmpeg output:\n{log}")
        lm = json.loads(blocks[-1])
        measured = (
            f"measured_I={lm['input_i']}:measured_TP={lm['input_tp']}:measured_LRA={lm['input_lra']}"
            f":measured_thresh={lm['input_thresh']}:offset={lm['target_offset']}:linear=true"
        )
        log = _ffmpeg(
            [
                "-y",
                "-i",
                str(premix),
                "-af",
                f"loudnorm={target}:{measured}:print_format=json",
                "-ar",
                str(MIX_RATE),
                "-c:a",
                "pcm_s16le",
                str(out),
            ]
        )
        mode = json.loads(re.findall(r"\{[^{}]*\}", log)[-1])["normalization_type"]
        levels = {name: measure(path) for name, path in stems.items()}
        end_window = (end_from, duration - fade_out_s) if end_from is not None else None
        music_end = (
            measure(stems["ducked_dry"], window=end_window)["lufs"]
            if track and end_window and end_window[1] - end_window[0] >= 0.4
            else None
        )
    o = measure(out)
    if sfx and o["tp"] > MIX_TP_LIMIT_DB:
        raise MusicError(
            f"The mix with SFX peaks at {o['tp']:.1f} dBTP, over the {MIX_TP_LIMIT_DB:g} dBTP limit "
            "(reelline/media/sfx/config.py MIX_TP_LIMIT_DB); turn the SFX down with --sfx-gain"
        )
    # The stems went through the same normalization as the premix; in linear mode this
    # gain is exact, in dynamic mode it's the average.
    norm_db = o["lufs"] - levels["mix"]["lufs"]
    return MixReport(
        offset if track else None,
        loops if track else 0,
        round(gain_db, 2) if track else None,
        round(levels["bed_dry"]["lufs"] - levels["ducked_dry"]["lufs"], 1) if track else None,
        round(levels["voice_dry"]["lufs"] + norm_db, 1),
        round(levels["ducked_dry"]["lufs"] + norm_db, 1) if track else None,
        o["lufs"],
        o["tp"],
        mode,
        tuple(applied),
        round(levels["sfx_dry"]["tp"] + norm_db, 1) if sfx else None,
        round(music_end + norm_db, 1) if music_end is not None else None,
    )
