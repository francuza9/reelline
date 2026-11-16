"""SFX library: assets/sfx/<category>/<id>.wav plus the license record assets/sfx/sfx.json.

sfx.json is a list with one entry per stored sound: id, category, file (the original
file name), source_url, license, author, added (UTC date), duration_s, and the
sound's measured levels after normalization (peak_db, dBFS sample peak; lufs, or
null for a sound too short to measure). The id is the original file's stem,
lowercased, with anything but [a-z0-9_] turned into "_".

load_library() checks the whole library and fails on any problem: an entry with a
license outside LICENSES, an unknown category, a missing file, a sound longer than
its category's max_duration_s, and any file in assets/sfx/ without an entry.
add_sound() trims edge silence, converts to 48 kHz stereo WAV, normalizes to the
category's target and records the entry.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from reelline.core.errors import ReellineError
from reelline.media.sfx.config import (
    CATALOG_NAME,
    CATEGORIES,
    CHANNELS,
    LICENSES,
    MAX_STORED_PEAK_DB,
    MIN_DURATION_S,
    RATE,
    SFX_DIR,
    TRIM_THRESHOLD_DB,
)

FIELDS = ("id", "category", "file", "source_url", "license", "author", "added", "duration_s", "peak_db", "lufs")
# ebur128 reports this for a sound with no 400 ms block above its gate.
NO_LOUDNESS_LUFS = -70.0
# A recorded duration may differ from the file's by this much (rounding).
DURATION_TOLERANCE_S = 0.002


class SfxError(ReellineError, RuntimeError):
    """Raised for a bad SFX library, a refused sound or a failed ffmpeg step."""


@dataclass(frozen=True)
class Sound:
    id: str
    category: str
    path: Path
    file: str
    source_url: str
    license: str
    author: str
    added: str
    duration_s: float
    peak_db: float
    lufs: float | None

    @property
    def level_db(self) -> float:
        """The sound's level in its category's measure (sample peak or LUFS)."""
        if CATEGORIES[self.category]["normalize"] == "peak":
            return self.peak_db
        if self.lufs is None:
            raise SfxError(f"{self.id}: category {self.category} is loudness-normalized but it has no LUFS reading")
        return self.lufs

    def entry(self) -> dict:
        return {k: getattr(self, k) for k in FIELDS}


def sound_id(file_name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", Path(file_name).stem.lower()).strip("_")


def check_license(license_name: str, source_url: str) -> None:
    """Raise SfxError unless the license is on the allowlist (exact name) and the
    source URL is an http(s) URL from a site the license allows."""
    if license_name not in LICENSES:
        allowed = ", ".join(f'"{name}"' for name in LICENSES)
        raise SfxError(f"License {license_name!r} is not allowed; use exactly one of {allowed}")
    url = urlparse(source_url)
    if url.scheme not in ("http", "https") or not url.hostname:
        raise SfxError(f"Source URL {source_url!r} is not an http(s) URL")
    hosts = LICENSES[license_name]
    if hosts and not any(url.hostname == h or url.hostname.endswith(f".{h}") for h in hosts):
        raise SfxError(f"{license_name!r} only covers sounds from {', '.join(hosts)}; got {url.hostname}")


def check_duration(category: str, duration_s: float, what: str) -> None:
    limit = CATEGORIES[category]["max_duration_s"]
    if duration_s > limit + DURATION_TOLERANCE_S:
        note = CATEGORIES[category].get("max_note")
        raise SfxError(
            f"{what} is {duration_s:.3f}s; {category} sounds may be at most {limit:g}s" + (f" ({note})" if note else "")
        )


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error) as exc:
        raise SfxError(f"Can't read {path}: {exc}") from exc


# --- library --------------------------------------------------------------------------


def catalog_path(root: Path) -> Path:
    return root / CATALOG_NAME


def _read_catalog(root: Path) -> list[dict]:
    path = catalog_path(root)
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SfxError(f"Can't read {path}: {exc}") from exc
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise SfxError(f"{path}: expected a list of sound entries")
    return entries


def _write_catalog(root: Path, sounds: list[Sound]) -> None:
    path = catalog_path(root)
    root.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    entries = [s.entry() for s in sorted(sounds, key=lambda s: (s.category, s.id))]
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_library(root: Path = SFX_DIR) -> list[Sound]:
    """Every sound in the library, checked (see the module docstring)."""
    path = catalog_path(root)
    sounds: list[Sound] = []
    for n, e in enumerate(_read_catalog(root), start=1):
        missing = [k for k in FIELDS if k not in e]
        if missing:
            raise SfxError(f"{path.name} entry {n}: missing {', '.join(missing)}")
        what = f"{path.name}: {e['id']}"
        if e["category"] not in CATEGORIES:
            raise SfxError(f"{what}: unknown category {e['category']!r} (known: {', '.join(CATEGORIES)})")
        try:
            check_license(e["license"], e["source_url"])
        except SfxError as exc:
            raise SfxError(f"{what}: {exc}") from None
        if not str(e["author"]).strip():
            raise SfxError(f"{what}: empty author")
        file = root / e["category"] / f"{e['id']}.wav"
        if not file.is_file():
            raise SfxError(f"{what}: file missing: {file}")
        duration = wav_duration(file)
        if abs(duration - float(e["duration_s"])) > DURATION_TOLERANCE_S:
            raise SfxError(f"{what}: recorded duration {e['duration_s']}s but the file is {duration:.3f}s")
        check_duration(e["category"], duration, what)
        sounds.append(
            Sound(
                e["id"],
                e["category"],
                file,
                e["file"],
                e["source_url"],
                e["license"],
                e["author"],
                e["added"],
                float(e["duration_s"]),
                float(e["peak_db"]),
                None if e["lufs"] is None else float(e["lufs"]),
            )
        )
    ids = [s.id for s in sounds]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SfxError(f"{path.name}: duplicate ids {', '.join(dupes)}")
    known = {s.path.resolve() for s in sounds} | {path.resolve()}
    if root.is_dir():
        strays = sorted(p for p in root.rglob("*") if p.is_file() and p.resolve() not in known)
        if strays:
            raise SfxError(
                f"No license entry in {path.name} for: {', '.join(str(p.relative_to(root)) for p in strays)} "
                "(add sounds with scripts/sfx.py add, never by copying files in)"
            )
    return sounds


def by_category(sounds: list[Sound], category: str) -> list[Sound]:
    return [s for s in sounds if s.category == category]


# --- add / remove ---------------------------------------------------------------------


def _ffmpeg(args: list[str]) -> str:
    if shutil.which("ffmpeg") is None:
        raise SfxError("ffmpeg not found on PATH; needed to process sounds")
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SfxError(f"ffmpeg failed:\n{result.stderr}")
    return result.stderr


def measure_levels(path: Path) -> tuple[float, float | None]:
    """(sample peak dBFS, integrated LUFS or None when too short or quiet to measure)."""
    log = _ffmpeg(["-i", str(path), "-af", "ebur128=framelog=quiet:peak=sample", "-f", "null", "-"])
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    peak = re.findall(r"Sample peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS", log)
    if not lufs or not peak:
        raise SfxError(f"No ebur128 summary in ffmpeg output for {path}:\n{log[-2000:]}")
    loudness = float(lufs[-1])
    return float(peak[-1]), None if loudness <= NO_LOUDNESS_LUFS else loudness


def _trim_filter() -> str:
    # silenceremove only trims the start, so trim, reverse, trim again, reverse back.
    head = f"silenceremove=start_periods=1:start_threshold={TRIM_THRESHOLD_DB}dB:start_silence=0:detection=peak"
    return f"{head},areverse,{head},areverse"


def add_sound(
    src: Path,
    category: str,
    source_url: str,
    license_name: str,
    author: str,
    root: Path = SFX_DIR,
    sid: str | None = None,
) -> Sound:
    """Process `src` into the library and record its license entry (see the module
    docstring). `sid` overrides the id made from the file name (same rules)."""
    if category not in CATEGORIES:
        raise SfxError(f"Unknown category {category!r} (known: {', '.join(CATEGORIES)})")
    check_license(license_name, source_url)
    if not author.strip():
        raise SfxError("--author is empty")
    if not src.is_file():
        raise SfxError(f"{src} doesn't exist")
    sounds = load_library(root)
    sid = sound_id(sid or src.name)
    if not sid:
        raise SfxError(f"Can't make an id from the file name {src.name!r}")
    if any(s.id == sid for s in sounds):
        raise SfxError(f"Id {sid!r} is already in the library; remove it first or rename the file")
    cfg = CATEGORIES[category]
    dest = root / category / f"{sid}.wav"
    with tempfile.TemporaryDirectory() as tmp:
        trimmed = Path(tmp) / "trimmed.wav"
        _ffmpeg(
            [
                "-y",
                "-i",
                str(src),
                "-vn",
                "-af",
                f"{_trim_filter()},aresample={RATE}",
                "-ac",
                str(CHANNELS),
                "-c:a",
                "pcm_f32le",
                str(trimmed),
            ]
        )
        duration = wav_duration_any(trimmed)
        if duration < MIN_DURATION_S:
            raise SfxError(f"{src.name} is silent (nothing above {TRIM_THRESHOLD_DB:g} dBFS)")
        check_duration(category, duration, f"{src.name} (after trimming silence)")
        peak, lufs = measure_levels(trimmed)
        if cfg["normalize"] == "peak":
            gain = cfg["target_db"] - peak
        elif lufs is None:
            raise SfxError(
                f"{src.name}: too short or quiet for a loudness reading, and {category} sounds are normalized "
                'to LUFS; switch the category to "peak" in reelline/media/sfx/config.py if that\'s intended'
            )
        else:
            gain = cfg["target_db"] - lufs
        if peak + gain > MAX_STORED_PEAK_DB:
            raise SfxError(
                f"{src.name}: normalizing to {cfg['target_db']:g} {'dBFS' if cfg['normalize'] == 'peak' else 'LUFS'} "
                f"would peak at {peak + gain:.1f} dBFS (limit {MAX_STORED_PEAK_DB:g}); lower the {category} target"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        _ffmpeg(
            [
                "-y",
                "-i",
                str(trimmed),
                "-af",
                f"volume={gain:.2f}dB",
                "-ar",
                str(RATE),
                "-ac",
                str(CHANNELS),
                "-c:a",
                "pcm_s16le",
                str(dest),
            ]
        )
    try:
        peak, lufs = measure_levels(dest)
        sound = Sound(
            sid,
            category,
            dest,
            src.name,
            source_url,
            license_name,
            author.strip(),
            datetime.now(UTC).date().isoformat(),
            round(wav_duration(dest), 3),
            peak,
            lufs,
        )
        _write_catalog(root, [*sounds, sound])
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return sound


def wav_duration_any(path: Path) -> float:
    """Duration of a float WAV (the wave module only reads PCM), via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SfxError(f"ffprobe failed on {path}:\n{result.stderr}")
    value = result.stdout.strip()
    return float(value) if value not in ("", "N/A") else 0.0


def remove_sound(sid: str, root: Path = SFX_DIR) -> Sound:
    """Delete a sound's file and its entry."""
    sounds = load_library(root)
    match = next((s for s in sounds if s.id == sid), None)
    if match is None:
        raise SfxError(f"No sound {sid!r} in the library")
    _write_catalog(root, [s for s in sounds if s.id != sid])
    match.path.unlink()
    return match
