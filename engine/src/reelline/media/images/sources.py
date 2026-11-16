"""Where candidate images come from: Pixabay, Wikimedia Commons, Pexels, and the
generated fallback when it's on (GENERATED["enabled"]; the library is in library.py).
Only these sources: no general web or image search, never photos from news articles.

Search functions return Candidates that passed the checks possible before
download (license allowlist, mime, resolution, aspect) and Rejects for the rest. download()
fetches the bytes; open_image() validates them (decodable, not animated).
"""

from __future__ import annotations

import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from PIL import Image

from reelline.config import conf
from reelline.config.credentials import PexelsSettings, PixabaySettings
from reelline.core.errors import ReellineError
from reelline.media.images.config import CANDIDATES, COMMONS, GENERATED, PEXELS, PIXABAY, STOPWORDS, Screen
from reelline.media.images.framing import aspect_problem, resolution_problem
from reelline.media.images.licenses import attribution_line, classify_commons, meta_value, short_author
from reelline.providers import CostLedger, LLMError, get_image_generator

EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}
USER_AGENT = f"{COMMONS['user_agent']} httpx/{httpx.__version__}"


class SourceError(ReellineError, RuntimeError):
    """Raised when a source's API fails or returns something unusable."""


@dataclass
class Candidate:
    source: str  # "commons" | "pixabay" | "pexels" | "generated" | "manual"
    source_key: str  # unique per source file, e.g. "commons:File:X.svg"
    title: str
    download_url: str | None
    page_url: str | None = None
    file_url: str | None = None
    license: str | None = None
    license_url: str | None = None
    author: str | None = None
    author_url: str | None = None
    attribution: str | None = None
    attribution_required: bool = False
    editorial_only: bool = False
    width: int | None = None
    height: int | None = None
    mime: str | None = None
    data: bytes | None = field(default=None, repr=False)  # generated images come with bytes
    # What the source says the file shows besides its title (Commons categories);
    # named-entity queries match against these (library.subject_names()).
    subjects: list[str] = field(default_factory=list)

    @property
    def names(self) -> str:
        """The title and subjects, for query.names_entity()."""
        title = self.title.removeprefix("File:")
        return " | ".join([Path(title).stem if self.source == "commons" else title, *self.subjects])


@dataclass(frozen=True)
class Reject:
    source: str
    source_key: str
    title: str
    reason: str
    page_url: str | None = None
    license: str | None = None


def open_image(data: bytes) -> tuple[Image.Image, str]:
    """(loaded image, file extension); SourceError if undecodable or animated."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as exc:  # Pillow raises many types for bad data
        raise SourceError(f"not a decodable image: {exc}") from None
    if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
        raise SourceError("animated image")
    if im.format not in EXTENSIONS:
        raise SourceError(f"unsupported image format {im.format}")
    return im, EXTENSIONS[im.format]


class Http:
    """One serial HTTP client with a descriptive User-Agent and a minimum gap
    between requests (Wikimedia API:Etiquette)."""

    def __init__(self, min_interval_s: float, timeout_s: float, headers: dict | None = None):
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, **(headers or {})},
            timeout=timeout_s,
            follow_redirects=True,
        )
        self._gap = min_interval_s
        self._last = 0.0

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(COMMONS["retries"] + 1):
            wait = self._last + self._gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                raise SourceError(f"GET {url}: {type(exc).__name__}: {exc}") from None
            finally:
                self._last = time.monotonic()
            lagged = response.headers.get("MediaWiki-API-Error") == "maxlag"
            if (response.status_code in (429, 503) or lagged) and attempt < COMMONS["retries"]:
                retry = float(response.headers.get("Retry-After") or COMMONS["retry_wait_s"])
                print(f"  {url}: {'maxlag' if lagged else response.status_code}, retrying in {retry:g}s")
                time.sleep(retry)
                continue
            if response.status_code != 200:
                raise SourceError(f"GET {url}: HTTP {response.status_code}: {response.text[:200]}")
            return response
        raise SourceError(f"GET {url}: still failing after {COMMONS['retries']} retries")

    def download(self, url: str) -> bytes:
        response = self.get(url)
        kind = response.headers.get("Content-Type", "")
        if not kind.startswith("image/"):
            raise SourceError(f"{url}: expected an image, got Content-Type {kind!r}")
        return response.content


# --- Wikimedia Commons ---------------------------------------------------------------


class Commons:
    def __init__(self):
        self.http = Http(COMMONS["min_interval_s"], COMMONS["timeout_s"])

    def search(self, query: str, screen: Screen) -> list[Candidate | Reject]:
        """File-namespace search, imageinfo with extmetadata in the same request."""
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "maxlag": str(COMMONS["maxlag_s"]),
            "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap|drawing",
            "gsrnamespace": "6",
            "gsrlimit": str(CANDIDATES["fetch"]),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": str(COMMONS["download_width"]),
            "iiextmetadatafilter": "|".join(
                (
                    "LicenseShortName",
                    "LicenseUrl",
                    "License",
                    "Artist",
                    "Credit",
                    "AttributionRequired",
                    "UsageTerms",
                    "Copyrighted",
                    "Restrictions",
                    "NonFree",
                    "Categories",
                )
            ),
        }
        body = self.http.get(COMMONS["api"], params).json()
        if "error" in body:
            raise SourceError(f"Commons API error: {body['error'].get('code')}: {body['error'].get('info')}")
        pages = sorted(body.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        return [self._candidate(p, screen) for p in pages if p.get("imageinfo")]

    @staticmethod
    def _candidate(page: dict, screen: Screen) -> Candidate | Reject:
        title = page["title"]
        info = page["imageinfo"][0]
        key = f"commons:{title}"
        page_url = info.get("descriptionurl")
        mime = info.get("mime", "")
        if mime not in COMMONS["mimes"]:
            return Reject("commons", key, title, f"unsupported type {mime}", page_url)
        meta = info.get("extmetadata") or {}
        verdict = classify_commons(meta)
        if not verdict.ok:
            return Reject("commons", key, title, verdict.reason, page_url, verdict.name)
        is_svg = mime == "image/svg+xml"
        # SVGs are rendered at the thumb width; bitmaps are never upscaled.
        width, height = (info.get("thumbwidth"), info.get("thumbheight")) if is_svg else (info["width"], info["height"])
        problem = resolution_problem(width, height, screen) or (None if is_svg else aspect_problem(width, height))
        if problem:
            return Reject("commons", key, title, problem, page_url, verdict.name)
        author = meta_value(meta, "Artist") or meta_value(meta, "Credit")
        if verdict.attribution_required and not author:
            return Reject("commons", key, title, "attribution required but no author/credit", page_url, verdict.name)
        use_thumb = is_svg or info["width"] > COMMONS["download_width"]
        categories = [c.strip() for c in meta_value(meta, "Categories").split("|") if c.strip()]
        return Candidate(
            source="commons",
            source_key=key,
            title=title,
            download_url=info["thumburl"] if use_thumb else info["url"],
            page_url=page_url,
            file_url=info["url"],
            license=verdict.name,
            license_url=verdict.url,
            author=short_author(author) if author else None,
            attribution=attribution_line("commons", author, verdict.name),
            attribution_required=verdict.attribution_required,
            editorial_only=verdict.editorial_only,
            width=width,
            height=height,
            mime=mime,
            subjects=categories,
        )


# --- Pexels ---------------------------------------------------------------------------


class Pexels:
    def __init__(self, settings: PexelsSettings):
        self.http = Http(0.0, PEXELS["timeout_s"], {"Authorization": settings.api_key})

    def search(self, query: str, screen: Screen) -> list[Candidate | Reject]:
        body = self.http.get(
            PEXELS["api"],
            {
                "query": query,
                "orientation": PEXELS["orientation"],
                "locale": PEXELS["locale"],
                "per_page": str(CANDIDATES["fetch"]),
                "page": "1",
            },
        ).json()
        out: list[Candidate | Reject] = []
        for photo in body.get("photos", []):
            key = f"pexels:{photo['id']}"
            title = photo.get("alt") or f"Pexels {photo['id']}"
            problem = resolution_problem(photo["width"], photo["height"], screen) or aspect_problem(
                photo["width"], photo["height"]
            )
            if problem:
                out.append(Reject("pexels", key, title, problem, photo["url"], PEXELS["license"]))
                continue
            out.append(
                Candidate(
                    source="pexels",
                    source_key=key,
                    title=title,
                    download_url=f"{photo['src']['original']}?auto=compress&cs=tinysrgb&w={PEXELS['download_width']}",
                    page_url=photo["url"],
                    file_url=photo["src"]["original"],
                    license=PEXELS["license"],
                    license_url=PEXELS["license_url"],
                    author=photo["photographer"],
                    author_url=photo["photographer_url"],
                    attribution=attribution_line("pexels", photo["photographer"], PEXELS["license"]),
                    # Not a license requirement; Pexels' API terms ask to credit photographers.
                    attribution_required=True,
                    width=photo["width"],
                    height=photo["height"],
                    mime="image/jpeg",
                )
            )
        return out


# --- Pixabay --------------------------------------------------------------------------


class Pixabay:
    """Photo search (pixabay.com/api/docs): safesearch on, photos only, oriented like the
    screen. Each response is cached in the library for PIXABAY["cache_s"] (their terms
    ask for 24 h); files are downloaded to the library, never hotlinked."""

    def __init__(self, settings: PixabaySettings, library=None):
        self.http = Http(PIXABAY["min_interval_s"], PIXABAY["timeout_s"])
        self._key = settings.api_key
        self.library = library  # for the search cache (None: no cache)

    def request(self, query: str, screen: Screen) -> dict:
        """The search parameters, without the key."""
        return {
            # Pixabay matches every word against its tags: stopwords only lose results.
            "q": " ".join(w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOPWORDS)[:100],
            "image_type": PIXABAY["image_type"],
            "safesearch": PIXABAY["safesearch"],
            "lang": PIXABAY["lang"],
            "orientation": "horizontal" if screen.aspect >= 1 else "vertical",
            "per_page": str(max(3, CANDIDATES["fetch"])),
        }

    def search(self, query: str, screen: Screen) -> list[Candidate | Reject]:
        params = self.request(query, screen)
        cache_key = json.dumps(params, sort_keys=True)
        body = self.library.search_get("pixabay", cache_key, PIXABAY["cache_s"]) if self.library else None
        if body is None:
            try:
                body = self.http.get(PIXABAY["api"], {**params, "key": self._key}).json()
            except SourceError as exc:
                raise SourceError(str(exc).replace(self._key, "<key>")) from None
            if self.library:
                self.library.search_put("pixabay", cache_key, body)
        return [self._candidate(hit, screen) for hit in body.get("hits", [])]

    @staticmethod
    def _candidate(hit: dict, screen: Screen) -> Candidate | Reject:
        key = f"pixabay:{hit['id']}"
        title = hit.get("tags") or f"Pixabay {hit['id']}"
        page = hit.get("pageURL")
        # largeImageURL is the original scaled to at most large_px on its long side.
        w, h = hit["imageWidth"], hit["imageHeight"]
        scale = min(1.0, PIXABAY["large_px"] / max(w, h))
        width, height = round(w * scale), round(h * scale)
        problem = resolution_problem(width, height, screen) or aspect_problem(width, height)
        if problem:
            return Reject("pixabay", key, title, problem, page, PIXABAY["license"])
        user = hit.get("user") or "Pixabay"
        profile = f"https://pixabay.com/users/{user}-{hit['user_id']}/" if hit.get("user_id") else None
        return Candidate(
            source="pixabay",
            source_key=key,
            title=title,
            download_url=hit["largeImageURL"],
            page_url=page,
            file_url=hit["largeImageURL"],
            license=PIXABAY["license"],
            license_url=PIXABAY["license_url"],
            author=user,
            author_url=profile,
            attribution=attribution_line("pixabay", user, PIXABAY["license"]),
            # The Pixabay Content License needs no attribution; the credits page still
            # names the photographer and links Pixabay, like every source.
            attribution_required=False,
            width=width,
            height=height,
            mime="image/jpeg",
        )


# --- generated fallback (off unless GENERATED["enabled"]) -------------------------------


def generate(client, query: str, seed: int, calls: list[dict] | None = None) -> Candidate:
    """A flat night-palette illustration of the query (the illustrator role, models.illustrator);
    the call's cost row (purpose image_generate, or image_generate_upper_bound without the usage's
    modality breakdown: reelline/providers/google.py) goes to `calls` when given."""
    prompt = f"{GENERATED['style']}\nSubject: {query}."
    model = conf().models.illustrator.model
    try:
        image = get_image_generator("illustrator", client).generate(
            prompt, seed=seed, ledger=CostLedger(rows=calls) if calls is not None else None
        )
    except LLMError as exc:
        raise SourceError(f"Image generation failed: {exc}") from exc.__cause__
    if image is None:
        raise SourceError(f"Image generation returned no image for {query!r}")
    data = image.data
    return Candidate(
        source="generated",
        source_key=f"generated:{model}:{seed}:{query}",
        title=f"generated: {query}",
        download_url=None,
        license="generated",
        author=None,
        attribution=None,
        attribution_required=False,
        mime="image/jpeg",
        data=data,
    )
