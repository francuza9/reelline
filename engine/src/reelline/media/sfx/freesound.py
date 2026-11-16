"""SFX candidates from Freesound (CC0 only): search, download previews, send to
Telegram, and pick into the library.

fetch_candidates() searches each of the category's queries (FETCH) with a CC0
license filter and the category's duration limit, sorted by FREESOUND["sort"];
checks each result's license field again (anything but CC0 is dropped, whatever
the filter did) and its duration; drops ids seen in this run, ids already in the
library (a Freesound source URL in sfx.json) and ids in rejected.json; takes the
queries' results in turn up to the run's size; and downloads each preview to
assets_src/sfx/candidates/<category>/<freesound_id>.mp3 with a JSON sidecar (id,
name, username, license, url, duration). run.json lists the run's candidates in
order: their numbers in Telegram and for pick_candidates().

pick_candidates() adds the chosen numbers through the library's add path (trim,
convert, normalize, limits) with source URL, license ("CC0") and author from the
sidecars (a number can go into another category: `into`), and marks every other
candidate of the run rejected, except one already in the library. A pick that
fails the library's checks is rejected too, with the reason.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from reelline.config import conf
from reelline.config.credentials import FreesoundSettings
from reelline.media import __version__
from reelline.media.sfx.config import CANDIDATES_DIR, CATEGORIES, FETCH, FREESOUND, REJECTED_NAME, SFX_DIR
from reelline.media.sfx.library import SfxError, Sound, add_sound, load_library

RUN_NAME = "run.json"
LICENSE = "CC0"  # the library's name for it (reelline/media/sfx/config.py LICENSES)
# Older API responses gave the license as its deed URL.
_CC0_URL = re.compile(r"^https?://creativecommons\.org/publicdomain/zero/1\.0/?$")
_SOUND_URL = re.compile(r"freesound\.org/(?:people/[^/]+/)?sounds/(\d+)")


class FreesoundError(SfxError):
    """Raised when the Freesound API fails or returns something unusable."""


@dataclass(frozen=True)
class Result:
    """A search result that passed every check."""

    id: int
    name: str
    username: str
    license: str
    url: str
    duration: float
    preview_url: str
    query: str

    def sidecar(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "username": self.username,
            "license": self.license,
            "url": self.url,
            "duration": self.duration,
            "query": self.query,
        }


class Client(Protocol):
    def search(self, query: str, search_filter: str) -> dict: ...

    def download(self, url: str) -> bytes: ...


# --- API --------------------------------------------------------------------------------


class FreesoundClient:
    """Token-authenticated, serial, with a minimum gap between requests and a retry on 429."""

    def __init__(self, settings: FreesoundSettings):
        self._key = settings.api_key
        self._client = httpx.Client(
            headers={"User-Agent": f"{conf().channel.robots_agent}/{__version__} httpx/{httpx.__version__}"},
            timeout=FREESOUND["timeout_s"],
            follow_redirects=True,
        )
        self._last = 0.0

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "<key>")

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        # The token only goes to freesound.org (the API and its preview CDN).
        host = (urlparse(url).hostname or "").lower()
        auth = host == "freesound.org" or host.endswith(".freesound.org")
        headers = {"Authorization": f"Token {self._key}"} if auth else {}
        for attempt in range(FREESOUND["retries"] + 1):
            wait = self._last + FREESOUND["min_interval_s"] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise FreesoundError(f"GET {url}: {type(exc).__name__}: {self._redact(str(exc))}") from None
            finally:
                self._last = time.monotonic()
            if response.status_code == 429 and attempt < FREESOUND["retries"]:
                retry = float(response.headers.get("Retry-After") or FREESOUND["retry_wait_s"])
                print(f"  Freesound: throttled (429), retrying in {retry:g}s", flush=True)
                time.sleep(retry)
                continue
            if response.status_code != 200:
                raise FreesoundError(f"GET {url}: HTTP {response.status_code}: {self._redact(response.text[:300])}")
            return response
        raise FreesoundError(f"GET {url}: still throttled after {FREESOUND['retries']} retries")

    def search(self, query: str, search_filter: str, fields: str | None = None, page_size: int | None = None) -> dict:
        try:
            return self._get(
                FREESOUND["api"],
                {
                    "query": query,
                    "filter": search_filter,
                    "sort": FREESOUND["sort"],
                    "fields": fields or FREESOUND["fields"],
                    "page_size": str(page_size or FREESOUND["results_per_query"]),
                },
            ).json()
        except ValueError:
            raise FreesoundError(f"Freesound search {query!r}: non-JSON reply") from None

    def sound(self, sound_id: int, fields: str | None = None) -> dict:
        """One sound's record (GET /apiv2/sounds/<id>/)."""
        try:
            return self._get(
                FREESOUND["sound_api"].format(id=sound_id), {"fields": fields or FREESOUND["fields"]}
            ).json()
        except ValueError:
            raise FreesoundError(f"Freesound sound {sound_id}: non-JSON reply") from None

    def download(self, url: str) -> bytes:
        response = self._get(url)
        kind = response.headers.get("Content-Type", "")
        if not kind.startswith("audio/"):
            raise FreesoundError(f"{url}: expected audio, got Content-Type {kind!r}")
        return response.content


# --- search and checks ------------------------------------------------------------------


def search_filter(max_duration_s: float) -> str:
    """Solr filter: CC0, at most `max_duration_s`, and the popularity minimums."""
    parts = [f'license:"{FREESOUND["license_filter"]}"', f"duration:[0 TO {max_duration_s:g}]"]
    if FREESOUND["min_downloads"]:
        parts.append(f"num_downloads:[{FREESOUND['min_downloads']} TO *]")
    if FREESOUND["min_avg_rating"]:
        parts.append(f"avg_rating:[{FREESOUND['min_avg_rating']:g} TO *]")
    return " ".join(parts)


def is_cc0(license_value: object) -> bool:
    value = str(license_value or "").strip()
    return value == FREESOUND["license_filter"] or bool(_CC0_URL.match(value))


def check_result(raw: dict, max_duration_s: float, query: str) -> Result | str:
    """A Result, or why the raw search result is dropped."""
    if not is_cc0(raw.get("license")):
        return f"license {raw.get('license')!r} is not CC0"
    try:
        duration = float(raw["duration"])
        result = Result(
            int(raw["id"]),
            str(raw["name"]).strip(),
            str(raw["username"]).strip(),
            LICENSE,
            str(raw["url"]),
            round(duration, 3),
            str((raw.get("previews") or {})[FREESOUND["preview"]]),
            query,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return f"incomplete result ({type(exc).__name__}: {exc})"
    if not 0 < duration <= max_duration_s:
        return f"duration {duration:.2f}s is over {max_duration_s:g}s"
    if not _SOUND_URL.search(result.url):
        return f"page URL {result.url!r} isn't a Freesound sound page"
    return result


def freesound_id(source_url: str) -> int | None:
    m = _SOUND_URL.search(source_url)
    return int(m.group(1)) if m else None


def library_ids(sounds: list[Sound]) -> set[int]:
    """Freesound ids already in the library (from their source URLs)."""
    return {i for i in (freesound_id(s.source_url) for s in sounds) if i is not None}


def select(per_query: list[list[Result]], n: int, exclude: set[int]) -> list[Result]:
    """Up to `n` results, one from each query in turn, skipping `exclude` and repeats."""
    queues = [list(results) for results in per_query]
    seen = set(exclude)
    out: list[Result] = []
    while len(out) < n and any(queues):
        for queue in queues:
            while queue and queue[0].id in seen:
                queue.pop(0)
            if queue and len(out) < n:
                result = queue.pop(0)
                seen.add(result.id)
                out.append(result)
    return out


# --- rejected.json ----------------------------------------------------------------------


def load_rejected(root: Path = CANDIDATES_DIR) -> dict[str, dict]:
    path = root / REJECTED_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SfxError(f"Can't read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SfxError(f"{path}: expected an object keyed by Freesound id")
    return data


def save_rejected(rejected: dict[str, dict], root: Path = CANDIDATES_DIR) -> None:
    root.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(rejected.items(), key=lambda kv: int(kv[0])))
    (root / REJECTED_NAME).write_text(json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --- fetch ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fetched:
    candidates: list[tuple[Result, Path]]  # in run order (number = index + 1)
    dropped: list[tuple[str, str]]  # (what, why) for results that failed a check
    skipped: int  # results that passed the checks but are already in the library or rejected


def fetch_candidates(
    client: Client,
    category: str,
    queries: list[str] | None = None,
    n: int | None = None,
    library_root: Path = SFX_DIR,
    root: Path = CANDIDATES_DIR,
    log=print,
) -> Fetched:
    """Search, check, select and download a run of candidates (see the module docstring).
    The category's previous run (its previews, sidecars and run.json) is replaced."""
    if category not in FETCH:
        raise SfxError(f"No fetch presets for category {category!r} (known: {', '.join(FETCH)})")
    preset = FETCH[category]
    queries = list(queries or preset["queries"])
    n = n or FREESOUND["candidates_per_run"]
    if n < 1:
        raise SfxError(f"--n must be at least 1, got {n}")
    exclude = library_ids(load_library(library_root)) | {int(i) for i in load_rejected(root)}
    flt = search_filter(preset["max_duration_s"])
    per_query: list[list[Result]] = []
    dropped: list[tuple[str, str]] = []
    for query in queries:
        body = client.search(query, flt)
        raw = body.get("results")
        if not isinstance(raw, list):
            raise FreesoundError(f"Freesound search {query!r}: no results list in the reply")
        kept = []
        for item in raw:
            checked = check_result(item, preset["max_duration_s"], query)
            if isinstance(checked, str):
                dropped.append((f"{item.get('id')} {item.get('name', '')!r}", checked))
            else:
                kept.append(checked)
        per_query.append(kept)
        log(f"  {query!r}: {body.get('count', '?')} match(es), {len(raw)} fetched, {len(kept)} passed the checks")
    chosen = select(per_query, n, exclude)
    skipped = len({r.id for q in per_query for r in q} & exclude)

    out_dir = root / category
    if out_dir.exists():
        for old in out_dir.iterdir():
            if old.suffix in (".mp3", ".json") and old.is_file():
                old.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates = []
    for result in chosen:
        mp3 = out_dir / f"{result.id}.mp3"
        mp3.write_bytes(client.download(result.preview_url))
        mp3.with_suffix(".json").write_text(
            json.dumps(result.sidecar(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        candidates.append((result, mp3))
    run = {
        "category": category,
        "fetched": datetime.now(UTC).isoformat(timespec="seconds"),
        "queries": queries,
        "candidates": [r.id for r, _ in candidates],
        "picked": None,
    }
    (out_dir / RUN_NAME).write_text(json.dumps(run, indent=1) + "\n", encoding="utf-8")
    return Fetched(candidates, dropped, skipped)


def caption(number: int, result: Result) -> str:
    name = result.name
    limit = FREESOUND["name_max_chars"]
    if len(name) > limit:
        name = name[: limit - 1].rstrip() + "…"
    return f"{number}. {name} - {result.duration:.2f}s - by {result.username}"


def send_candidates(notifier, category: str, candidates: list[tuple[Result, Path]]) -> None:
    """Each candidate as a numbered audio message, then one summary message (reelline/notify/)."""
    for number, (result, mp3) in enumerate(candidates, start=1):
        notifier.send_file(
            mp3.name,
            mp3,
            kind="audio",
            caption=caption(number, result),
            mime="audio/mpeg",
            meta={"title": f"{number}. {result.name}"[:64], "performer": result.username},
        )
    lines = [f"{category} candidates (Freesound, CC0):"]
    lines += [caption(number, result) for number, (result, _) in enumerate(candidates, start=1)]
    lines.append(f"Pick: python scripts/sfx.py pick --category {category} N [N ...]")
    notifier.send("\n".join(lines))


# --- pick -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Picked:
    added: list[Sound]
    failed: list[tuple[int, str]]  # (number, reason)
    rejected: list[int]  # Freesound ids marked rejected (not picked, or failed)


def load_run(category: str, root: Path = CANDIDATES_DIR) -> tuple[dict, list[dict]]:
    """The category's last run and its candidates' sidecars, in run order."""
    out_dir = root / category
    path = out_dir / RUN_NAME
    if not path.is_file():
        raise SfxError(f"No {category} candidates: {path} not found; run scripts/sfx.py fetch --category {category}")
    run = json.loads(path.read_text(encoding="utf-8"))
    sidecars = []
    for fid in run["candidates"]:
        side, mp3 = out_dir / f"{fid}.json", out_dir / f"{fid}.mp3"
        if not side.is_file() or not mp3.is_file():
            raise SfxError(f"Candidate {fid} is missing its preview or sidecar in {out_dir}; fetch again")
        sidecars.append(json.loads(side.read_text(encoding="utf-8")))
    return run, sidecars


def pick_candidates(
    category: str,
    numbers: list[int],
    library_root: Path = SFX_DIR,
    root: Path = CANDIDATES_DIR,
    into: dict[int, str] | None = None,
) -> Picked:
    """Add candidates `numbers` (1-based, the run's order) to the library, and the
    candidates in `into` ({number: category}) to another category; reject the rest,
    except candidates already in the library (added from another run or by hand)."""
    into = dict(into or {})
    for target in {category, *into.values()}:
        if target not in CATEGORIES:
            raise SfxError(f"Unknown category {target!r} (known: {', '.join(CATEGORIES)})")
    run, sidecars = load_run(category, root)
    if run.get("picked") is not None:
        raise SfxError(f"The last {category} run was already picked from ({run['picked']}); fetch again")
    count = len(sidecars)
    chosen = [*numbers, *into]
    bad = [n for n in chosen if not 1 <= n <= count]
    if bad or not chosen:
        raise SfxError(f"Pick numbers from 1..{count}" + (f"; got {bad}" if bad else ""))
    if len(set(chosen)) != len(chosen):
        raise SfxError(f"Repeated numbers in {sorted(chosen)}")
    bad_license = [n for n, side in enumerate(sidecars, start=1) if n in chosen and side.get("license") != LICENSE]
    if bad_license:
        raise SfxError(f"Candidates {bad_license}: the sidecar license isn't {LICENSE}")
    added, failed, rejected_now = [], [], []
    rejected = load_rejected(root)
    today = datetime.now(UTC).date().isoformat()

    def reject(side: dict, reason: str) -> None:
        rejected[str(side["id"])] = {"category": category, "name": side["name"], "reason": reason, "date": today}
        rejected_now.append(int(side["id"]))

    in_library = library_ids(load_library(library_root))
    for number, side in enumerate(sidecars, start=1):
        if number not in chosen:
            if int(side["id"]) not in in_library:
                reject(side, "not picked")
            continue
        target = into.get(number, category)
        try:
            added.append(
                add_sound(
                    root / category / f"{side['id']}.mp3",
                    target,
                    side["url"],
                    LICENSE,
                    side["username"],
                    library_root,
                    sid=f"{target}_fs{side['id']}",
                )
            )
        except SfxError as exc:
            failed.append((number, str(exc)))
            reject(side, f"add failed: {exc}".splitlines()[0])
    save_rejected(rejected, root)
    run["picked"] = [*sorted(numbers), *(f"{n}:{c}" for n, c in sorted(into.items()))]
    (root / category / RUN_NAME).write_text(json.dumps(run, indent=1) + "\n", encoding="utf-8")
    return Picked(added, failed, rejected_now)
