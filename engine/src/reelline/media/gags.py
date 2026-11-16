"""The cold open (channel docs/gags.md): before the reel, a ~1.5 s gag of the host caught doing something
embarrassing (dancing for TikTok, rage-quitting a game, crying at a rom-com...), then a hard cut into
the reel as it always was: for retention, something happens in the very first second.

remotion/src/gags.json is the config (the mode and the A/B share, the timing, the placement, the
camera, each gag's loop and sound), read here and by remotion/src/ColdOpen.tsx; the art is
assets/gags/ (tools/gags/build.py), the sounds the SFX library's gag and scratch categories
(tools/gags/sfx.py).

Which reels get one (decide()), all seeded by the reel's name like the motion planner:
  - none in the straight style (reelline/media/style.py STYLE_MODE, since 2026-10-31), whatever gags.json says
    (mode_of()); in the classic style gags.json's mode: "ab": about abShare of the reels (in_ab(): the
    name's own draw, so it never changes for a reel), the others none; "on": every reel; "off": none.
    scripts/render_reel.py --gag overrides it for one render (auto: as above, none, or a gag's name).
  - which gag: a seeded pick among the gags not among the last noRepeat reels' gags (recent_gags():
    the reels table, rendered / approved / published, newest first, the reel itself not counted).
The decision is recorded on the reel (reels.gag, gag_s, gag_mode; reelline/pipeline/produce.py) so the
retention reports can compare reels with and without one (reelline/pipeline/retention.py, analytics.py).

The reel's timeline doesn't move: Remotion plays the reel in a Sequence from the gag's last frame
(remotion/src/Reel.tsx), so every time in the props (captions, cards, the camera and its angles, the
end card, the share arrow) stays the reel's own and the video is the gag's frames longer. The audio is
the gag's sound (gag_audio(): the action sound to the caught pose, the record scratch on it, no music)
followed by the reel's own mix (with_gag()), so the voice, the music and every SFX start at the cut.
"""

from __future__ import annotations

import array
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.paths import REMOTION_DIR, REPO_ROOT, graphics_file


def _seed_prefix() -> str:
    """The channel's seed prefix (graphics/brand.json, the same one the Remotion motion seeds from)."""
    path = graphics_file(conf().channel.graphics_dir, "brand.json")
    return str(json.loads(path.read_text(encoding="utf-8"))["seedPrefix"])


GAGS_JSON = REMOTION_DIR / "src" / "gags.json"
ASSETS = conf().channel.dir / "assets"
GAG_DIR = ASSETS / "gags"
MODES = ("ab", "on", "off")
FPS = 30
FRAME = (1080, 1920)
# The reels whose gags count for noRepeat (the ones that are or will be seen).
HISTORY_STATUSES = ("rendered", "approved", "published", "partially_published")
LOOP_KEYS = ("periodS", "steps", "bobPx", "shakePx", "tiltDeg", "squash", "swell")
# The gag's sound, as the reel's mix is (reelline/media/music.py MIX_RATE, 16-bit stereo).
RATE = 48000
CHANNELS = 2


class GagError(ReellineError, ValueError):
    """gags.json can't be read or doesn't hold what this module reads, a gag's art or sound is missing,
    or the gag's audio can't be made."""


@dataclass(frozen=True)
class Gag:
    name: str
    sound: str
    gain_db: float
    loop: dict
    tv: dict | None  # {"cropY"}: the action beat from behind, the gag's picture on the wall TV

    def files(self) -> dict[str, Path]:
        """The gag's art under assets/gags/: action (the back pose for a TV gag), caught, screen."""
        out = {
            "action": GAG_DIR / f"{self.name}_{'back' if self.tv else 'action'}.png",
            "caught": GAG_DIR / f"{self.name}_caught.png",
        }
        if self.tv:
            out["screen"] = GAG_DIR / f"{self.name}_screen.jpg"
        return out


@dataclass(frozen=True)
class Config:
    mode: str
    ab_share: float
    no_repeat: int
    seed: str
    duration_s: float
    caught_s: float
    hold_from_s: float
    box: tuple[float, float]
    wheels_y: float
    center_x: float
    fade_out_s: float
    ceiling_dbfs: float
    scratch: str
    scratch_gain_db: float
    gags: dict[str, Gag]

    @property
    def frames(self) -> int:
        return round(self.duration_s * FPS)


def _frames(seconds: float, what: str) -> int:
    n = round(seconds * FPS)
    if abs(seconds * FPS - n) > 1e-6:
        raise GagError(f"{GAGS_JSON.name}: {what} {seconds:g}s isn't a whole number of frames at {FPS} fps")
    return n


def parse(spec: dict) -> Config:
    """gags.json's content, checked (load() reads the file)."""
    try:
        t, p, a = spec["timing"], spec["placement"], spec["audio"]
        gags = {}
        for name, g in spec["gags"].items():
            loop = {k: float(g["loop"][k]) for k in LOOP_KEYS}
            extra = set(g["loop"]) - set(LOOP_KEYS)
            if extra:
                raise GagError(
                    f"{GAGS_JSON.name}: gags.{name}.loop: unknown {', '.join(sorted(extra))} "
                    f"(have: {', '.join(LOOP_KEYS)})"
                )
            if loop["periodS"] <= 0 or loop["steps"] not in (0, 2):
                raise GagError(f"{GAGS_JSON.name}: gags.{name}.loop: periodS > 0 and steps 0 (smooth) or 2")
            tv = g["tv"]
            if tv is not None and not 0 <= float(tv["cropY"]) <= 1:
                raise GagError(f"{GAGS_JSON.name}: gags.{name}.tv.cropY must be in [0, 1]")
            gags[name] = Gag(
                name, str(g["sound"]), float(g["gainDb"]), loop, None if tv is None else {"cropY": float(tv["cropY"])}
            )
        cfg = Config(
            str(spec["mode"]),
            float(spec["abShare"]),
            int(spec["noRepeat"]),
            str(spec["seed"]).replace("{seedPrefix}", _seed_prefix()),
            float(t["durationS"]),
            float(t["caughtS"]),
            float(t["holdFromS"]),
            (float(p["boxPx"][0]), float(p["boxPx"][1])),
            float(p["wheelsY"]),
            float(p["centerX"]),
            float(a["fadeOutS"]),
            float(a["ceilingDbfs"]),
            str(spec["scratch"]["sound"]),
            float(spec["scratch"]["gainDb"]),
            gags,
        )
        for key in ("pushTo", "punchTo", "punchInS", "creepTo", "flashOpacity", "flashS"):
            float(spec["camera"][key])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, GagError):
            raise
        raise GagError(f"Can't read {GAGS_JSON}: {type(exc).__name__}: {exc}") from exc
    if cfg.mode not in MODES:
        raise GagError(f"{GAGS_JSON.name}: mode must be one of {', '.join(MODES)}, got {cfg.mode!r}")
    if not 0 <= cfg.ab_share <= 1:
        raise GagError(f"{GAGS_JSON.name}: abShare must be in [0, 1], got {cfg.ab_share:g}")
    if not cfg.gags:
        raise GagError(f"{GAGS_JSON.name}: no gags")
    if not 0 <= cfg.no_repeat < len(cfg.gags):
        raise GagError(
            f"{GAGS_JSON.name}: noRepeat must be 0..{len(cfg.gags) - 1} (there are {len(cfg.gags)} "
            f"gags), got {cfg.no_repeat}"
        )
    _frames(cfg.duration_s, "timing.durationS")
    _frames(cfg.caught_s, "timing.caughtS")
    if not 0 < cfg.caught_s < cfg.hold_from_s <= cfg.duration_s:
        raise GagError(
            f"{GAGS_JSON.name}: timing needs 0 < caughtS < holdFromS <= durationS, got "
            f"{cfg.caught_s:g}, {cfg.hold_from_s:g}, {cfg.duration_s:g}"
        )
    if cfg.box[0] <= 0 or cfg.box[1] <= 0 or not 0 < cfg.wheels_y <= FRAME[1] or not 0 < cfg.center_x < FRAME[0]:
        raise GagError(f"{GAGS_JSON.name}: placement: boxPx > 0, wheelsY and centerX inside the frame")
    if cfg.ceiling_dbfs > -1 or cfg.fade_out_s < 0:
        raise GagError(f"{GAGS_JSON.name}: audio: ceilingDbfs at most -1, fadeOutS >= 0")
    return cfg


@lru_cache(maxsize=1)
def load() -> Config:
    try:
        spec = json.loads(GAGS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GagError(f"Can't read {GAGS_JSON}: {exc}") from exc
    return parse(spec)


def check_files(cfg: Config, names: list[str] | None = None) -> None:
    """Raises GagError naming a gag's missing art (every gag's, or `names`')."""
    missing = [
        str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)
        for name in (names or cfg.gags)
        for path in cfg.gags[name].files().values()
        if not path.is_file()
    ]
    if missing:
        raise GagError(f"Cold open art missing (tools/gags/build.py makes it): {', '.join(missing)}")


def check_sounds(cfg: Config, sounds: list, names: list[str] | None = None) -> None:
    """Raises GagError when a gag's sound (every gag's, or `names`') or the scratch isn't in the SFX
    library in its category, or a category's longest sound wouldn't fit its beat."""
    from reelline.media.sfx.config import CATEGORIES

    by_id = {s.id: s for s in sounds}
    wanted = [(cfg.gags[n].sound, "gag", f"gags.{n}") for n in (names or cfg.gags)] + [
        (cfg.scratch, "scratch", "scratch")
    ]
    problems = [
        f"{where}.sound {sid!r} "
        + ("isn't in assets/sfx/sfx.json" if sid not in by_id else f"is a {by_id[sid].category} sound, not {category}")
        for sid, category, where in wanted
        if sid not in by_id or by_id[sid].category != category
    ]
    if CATEGORIES["gag"]["max_duration_s"] > cfg.caught_s + 1e-6:
        problems.append(
            f"gag sounds may run {CATEGORIES['gag']['max_duration_s']:g}s, past the caught pose at {cfg.caught_s:g}s"
        )
    if CATEGORIES["scratch"]["max_duration_s"] > cfg.duration_s - cfg.caught_s + 1e-6:
        problems.append(
            f"scratch sounds may run {CATEGORIES['scratch']['max_duration_s']:g}s, past the cut "
            f"({cfg.duration_s - cfg.caught_s:g}s after the caught pose)"
        )
    if problems:
        raise GagError(f"{GAGS_JSON.name}: " + "; ".join(problems))


# ---- Which reels, which gag ------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    gag: str | None
    mode: str  # the config's mode, or "forced" (--gag)
    why: str

    @property
    def arm(self) -> str:
        return "gag" if self.gag else "none"


def in_ab(name: str, cfg: Config) -> bool:
    """Whether the reel is in the A/B's gag arm: its own seeded draw under abShare."""
    return random.Random(f"{cfg.seed}/ab/{name}").random() < cfg.ab_share


def pick(name: str, recent: list[str | None], cfg: Config) -> str:
    """A seeded gag for the reel, none of the last noRepeat reels' gags (`recent`: newest first)."""
    used = {g for g in recent[: cfg.no_repeat] if g}
    pool = [g for g in sorted(cfg.gags) if g not in used] or sorted(cfg.gags)
    return random.Random(f"{cfg.seed}/pick/{name}").choice(pool)


def mode_of(cfg: Config) -> str:
    """The mode the reels are decided under: "off" in a style without the cold open (reelline/media/style.py:
    straight), else gags.json's."""
    from reelline.media import style

    return cfg.mode if style.current().gags else "off"


def decide(name: str, recent: list[str | None], cfg: Config, force: str | None = None) -> Decision:
    """The reel's cold open. force: None or "auto" (the mode, mode_of()), "none", or a gag's name."""
    from reelline.media import style

    if force not in (None, "auto"):
        if force == "none":
            return Decision(None, "forced", "--gag none")
        if force not in cfg.gags:
            raise GagError(f"Unknown gag {force!r} (none, auto or one of: {', '.join(sorted(cfg.gags))})")
        return Decision(force, "forced", f"--gag {force}")
    if not style.current().gags:
        return Decision(
            None, "off", f"the {style.current().name} style: no cold open (reelline/media/style.py STYLE_MODE)"
        )
    if cfg.mode == "off":
        return Decision(None, "off", "gags.json mode off")
    if cfg.mode == "ab" and not in_ab(name, cfg):
        return Decision(None, "ab", f"A/B: the no-gag arm ({1 - cfg.ab_share:.0%} of reels)")
    used = [g for g in recent[: cfg.no_repeat] if g]
    gag = pick(name, recent, cfg)
    head = "every reel (gags.json mode on)" if cfg.mode == "on" else f"A/B: the gag arm ({cfg.ab_share:.0%} of reels)"
    return Decision(
        gag, cfg.mode, head + (f"; not {', '.join(used)} (the last {cfg.no_repeat} reels')" if used else "")
    )


def recent_gags(conn: sqlite3.Connection, name: str, n: int) -> list[str | None]:
    """The gags of the last `n` reels that are or will be seen (HISTORY_STATUSES), newest first by
    creation, one per reel name, `name` itself not counted; None for a reel without one."""
    marks = ", ".join("?" * len(HISTORY_STATUSES))
    out: list[str | None] = []
    seen: set[str] = set()
    for row_name, gag in conn.execute(
        f"SELECT name, gag FROM reels WHERE status IN ({marks}) AND name != ? ORDER BY created_at DESC, id DESC",
        (*HISTORY_STATUSES, name),
    ):
        if row_name in seen:
            continue
        if len(out) >= n:
            break
        seen.add(row_name)
        out.append(gag)
    return out


def recent_gags_in(db_path: Path, name: str, n: int, log: Callable[[str], None] = print) -> list[str | None]:
    """recent_gags() from the database file, opened read-only; empty (with a note) without one (a dev
    checkout) or before its reels table has the gag column."""
    if not db_path.is_file():
        log(f"NOTE: no database at {db_path}; no cold open history (any gag may repeat)")
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return recent_gags(conn, name, n)
    except sqlite3.OperationalError as exc:
        log(f"NOTE: no cold open history in {db_path.name} ({exc}); any gag may repeat")
        return []
    finally:
        conn.close()


# ---- The props --------------------------------------------------------------------------------------


def placement(cfg: Config, gag: Gag) -> dict[str, dict]:
    """Each pose's box in the frame ({"left", "top", "width", "height"}): one scale for the gag's poses
    (the larger fits placement.boxPx), bottom on wheelsY, middle on centerX."""
    from PIL import Image

    sizes = {}
    for key in ("action", "caught"):
        with Image.open(gag.files()[key]) as im:
            sizes[key] = im.size
    shared = [sizes["caught"]] + ([] if gag.tv else [sizes["action"]])
    scale = min(cfg.box[0] / max(w for w, _ in shared), cfg.box[1] / max(h for _, h in shared))
    scales = {"caught": scale, "action": scale}
    if gag.tv:  # the back pose is a shot of its own: fitted alone
        w, h = sizes["action"]
        scales["action"] = min(cfg.box[0] / w, cfg.box[1] / h)
    out = {}
    for key, (w, h) in sizes.items():
        width, height = w * scales[key], h * scales[key]
        out[key] = {
            "left": round(cfg.center_x - width / 2, 1),
            "top": round(cfg.wheels_y - height, 1),
            "width": round(width, 1),
            "height": round(height, 1),
        }
    return out


def screen_image(cfg: Config, gag: Gag, face: tuple[float, float]) -> dict | None:
    """A TV gag's picture as a Remotion screenImages entry, over the whole gag: cover-fit to the TV's
    face (`face`: width, height), the free room cropped by tv.cropY (0: the top kept, 1: the bottom)."""
    if not gag.tv:
        return None
    from PIL import Image

    path = gag.files()["screen"]
    with Image.open(path) as im:
        width, height = im.size
    aspect = face[0] / face[1]
    if width / height > aspect:  # wider than the face: crop the sides, centred
        w = height * aspect / width
        crop = {"x": round((1 - w) / 2, 5), "y": 0.0, "w": round(w, 5), "h": 1.0}
    else:
        h = width / aspect / height
        crop = {"x": 0.0, "y": round((1 - h) * gag.tv["cropY"], 5), "w": 1.0, "h": round(h, 5)}
    return {
        "src": f"gags/{path.name}",
        "start": 0.0,
        "end": cfg.duration_s,
        "width": width,
        "height": height,
        "mode": "cover",
        "crop": crop,
        "focal": {"x": 0.5, "y": 0.5},
        "panelColor": None,
        "seed": f"gag:{gag.name}",
    }


def props(cfg: Config, name: str, face: tuple[float, float]) -> dict:
    """Remotion's `gag` prop (remotion/src/types.ts GagProps): its length (the reel starts after it),
    the caught pose's time, each pose's file and box, and a TV gag's picture."""
    gag = cfg.gags[name]
    boxes = placement(cfg, gag)
    files = gag.files()
    return {
        "name": name,
        "durationS": cfg.duration_s,
        "caughtS": cfg.caught_s,
        "view": "back" if gag.tv else "front",
        "action": {"src": f"gags/{files['action'].name}", **boxes["action"]},
        "caught": {"src": f"gags/{files['caught'].name}", **boxes["caught"]},
        "screen": screen_image(cfg, gag, face),
    }


def files(cfg: Config, name: str) -> list[Path]:
    """The gag's art a render stages into the Remotion public dir, under gags/."""
    return list(cfg.gags[name].files().values())


# ---- The sound -------------------------------------------------------------------------------------


def _ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise GagError(f"ffmpeg failed: {r.stderr.strip()[-600:]}")


def gain_db(sound, gain: float, cfg: Config) -> float:
    """The volume (dB) that puts `sound` (a reelline/media/sfx/library.py Sound) its category's gain_db plus
    `gain` from the reel's mix target (reelline/media/music.py MIX_LOUDNORM: I for a lufs sound, TP for a peak one)."""
    from reelline.media.music import MIX_LOUDNORM
    from reelline.media.sfx.config import CATEGORIES

    c = CATEGORIES[sound.category]
    reference = MIX_LOUDNORM["I"] if c["normalize"] == "lufs" else MIX_LOUDNORM["TP"]
    return round(reference + c["gain_db"] + gain - sound.level_db, 2)


def gag_audio(cfg: Config, name: str, sounds: list, out: Path) -> dict:
    """`out`: the gag's sound, exactly its frames long (RATE, 16-bit stereo): the gag's action sound
    from 0, cut at caughtS with a fadeOutS fade, and the record scratch from caughtS, each at its
    gain_db() and limited to ceilingDbfs; silence where neither plays. Returns what was used."""
    by_id = {s.id: s for s in sounds}
    gag = cfg.gags[name]
    action, scratch = by_id[gag.sound], by_id[cfg.scratch]
    total = cfg.frames * RATE // FPS
    caught = round(cfg.caught_s * RATE)
    va, vs = gain_db(action, gag.gain_db, cfg), gain_db(scratch, cfg.scratch_gain_db, cfg)
    limit = 10 ** (cfg.ceiling_dbfs / 20)
    limiter = f"alimiter=limit={limit:.6f}:attack=1:release=30:level=false:latency=true"
    fade = (
        f"afade=t=out:st={max(0.0, cfg.caught_s - cfg.fade_out_s):.3f}:d={cfg.fade_out_s:.3f},"
        if cfg.fade_out_s
        else ""
    )
    graph = (
        f"[0:a]aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,volume={va:.2f}dB,{limiter},"
        f"atrim=end_sample={caught},{fade}apad=whole_len={total}[a];"
        f"[1:a]aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vs:.2f}dB,{limiter},"
        f"adelay=delays={caught}S:all=1,apad=whole_len={total}[s];"
        f"[a][s]amix=inputs=2:duration=longest:normalize=0,atrim=end_sample={total}[out]"
    )
    _ffmpeg(
        [
            "-i",
            str(action.path),
            "-i",
            str(scratch.path),
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-ar",
            str(RATE),
            "-ac",
            str(CHANNELS),
            "-c:a",
            "pcm_s16le",
            str(out),
        ]
    )
    frames, _ = _wav_info(out)
    if frames != total:
        raise GagError(f"The gag's sound is {frames} samples, not {total}")
    return {"action": action.id, "action_db": va, "scratch": scratch.id, "scratch_db": vs, "scratch_at": cfg.caught_s}


def silent_audio(cfg: Config, out: Path) -> None:
    """`out`: silence as long as the gag, in gag_audio()'s format (a render with --no-sfx)."""
    with wave.open(str(out), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * CHANNELS * (cfg.frames * RATE // FPS))


def _wav_info(path: Path) -> tuple[int, tuple[int, int, int]]:
    with wave.open(str(path), "rb") as w:
        return w.getnframes(), (w.getframerate(), w.getnchannels(), w.getsampwidth())


def with_gag(gag_wav: Path, reel_audio: Path, out: Path) -> None:
    """`out`: the gag's sound, then the reel's audio (its mix, or the voice) sample for sample, so the
    reel starts exactly at the cut. The reel's audio is converted to RATE 16-bit stereo first when it
    isn't (a voice-only track)."""
    _, fmt = _wav_info(gag_wav)
    if fmt != (RATE, CHANNELS, 2):
        raise GagError(
            f"{gag_wav.name} is {fmt[0]} Hz, {fmt[1]} channel(s), {8 * fmt[2]}-bit; the gag's sound is "
            f"{RATE} Hz, {CHANNELS} channels, 16-bit"
        )
    with tempfile.TemporaryDirectory() as tmp:
        body = reel_audio
        if _wav_info(reel_audio)[1] != fmt:
            body = Path(tmp) / "reel.wav"
            _ffmpeg(["-i", str(reel_audio), "-ar", str(RATE), "-ac", str(CHANNELS), "-c:a", "pcm_s16le", str(body)])
        with wave.open(str(gag_wav), "rb") as g, wave.open(str(body), "rb") as b, wave.open(str(out), "wb") as o:
            o.setparams(g.getparams())
            o.writeframes(g.readframes(g.getnframes()))
            while chunk := b.readframes(RATE):
                o.writeframes(chunk)


def peak_dbfs(path: Path, start_s: float = 0.0, end_s: float | None = None) -> float:
    """The sample peak (dBFS) of a 16-bit WAV between start_s and end_s (for tests and the render log)."""
    import math

    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        w.setpos(round(start_s * rate))
        n = (w.getnframes() if end_s is None else round(end_s * rate)) - round(start_s * rate)
        x = array.array("h", w.readframes(max(0, n)))
    if sys.byteorder == "big":
        x.byteswap()
    peak = max((abs(v) for v in x), default=0)
    return 20 * math.log10(peak / 32768) if peak else float("-inf")
