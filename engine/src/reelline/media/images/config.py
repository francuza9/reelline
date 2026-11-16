"""Tunables for wall-TV images. Every number the image pipeline uses lives here,
except the screen geometry and Ken Burns zoom, which are shared with the Remotion
scene through remotion/src/screen.json (see screen())."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from reelline.config import conf
from reelline.media import __version__
from reelline.paths import REMOTION_DIR, REPO_ROOT

LIBRARY_DIR = conf().channel.dir / "data" / "image_library"
SEARCH_OUT_DIR = REPO_ROOT / "out" / "image_search"
SCREEN_JSON = REMOTION_DIR / "src" / "screen.json"

# Licensed sources, library first (reuse). The generated illustration fallback isn't one
# of them: it only runs with GENERATED["enabled"].
SOURCES = ("library", "pixabay", "pexels", "commons")

# The order they're tried in, by query type (reelline/media/images/query.py). A generic scene
# (classroom, laptop, students) prefers a modern stock photo over Commons' documentary
# ones. A named entity (a university, ministry, company, building, place, logo) never
# gets a stock photo standing in for it: the library's exact match, then Commons, whose
# file must name it; else the idle screen (add the real one with `images.py add`).
SOURCE_ORDER = {
    "generic": ("library", "pixabay", "pexels", "commons"),
    "named": ("library", "commons"),
}

CANDIDATES = {
    # Candidates are vision-checked in source and rank order and the first that passes
    # wins. At most this many paid vision calls per paragraph (cached results are
    # free and don't count); then the paragraph gets the idle screen.
    "max_vision_calls": 3,
    # At most this many candidates checked from each source.
    "per_source": 4,
    # Photos narrower than this (width / height) are skipped before any vision call:
    # the 21:9 screen would show a thin band of them. SVGs and library images known
    # to be logos/graphics are exempt (they're shown whole on a panel).
    "min_aspect": 0.8,
    # Search results fetched per source before filtering.
    "fetch": 20,
    # At most this many of a paragraph's paid calls go to library images, so a library
    # full of images not yet checked under the current prompt (each one a full call)
    # can't use up the paragraph before the search sources get a turn.
    "library_paid_calls": 2,
    # Don't show the previous paragraph's image again, unless both paragraphs ask for
    # the same @image value.
    "avoid_adjacent_repeat": True,
    # An extra picture doesn't get an image already chosen for a different @image value
    # anywhere in the reel (the same photo coming back would show).
    "avoid_reel_repeat": True,
    # Pictures after a paragraph's first (extra @image lines, the TV changing every ~3-4 s):
    # at most this many paid vision calls on each, and this many over the whole reel (the
    # first picture of each paragraph isn't limited by it). A full call is about $0.0016, so
    # 12 calls are at most ~$0.02 (a hard ceiling of $0.048 at call_ceiling_usd each); a slot
    # that runs out of calls keeps the previous picture.
    "extra_slot_max_vision_calls": 2,
    "extra_slots_vision_budget": 12,
    # Extra pictures starting less than this many seconds after the previous picture's start
    # are dropped (before any search, so they cost nothing): the TV doesn't flicker.
    "min_slot_s": 2.2,
}

LIBRARY = {
    # A generic query matches a library image when the normalized queries are equal
    # (a query it passed for before), or when at least this many of the query's content
    # words (not STOPWORDS or GENERIC_WORDS) are in its vision tags (all of them if the
    # query has fewer). A named-entity query matches only an image whose source names
    # that entity (query.py names_entity()), never by word overlap.
    "min_overlap": 2,
}

# Query words ignored for library matching.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "with",
        "from",
        "by",
        "into",
        "over",
        "under",
        "near",
        "its",
        "his",
        "her",
        "their",
        "is",
        "are",
        "this",
        "that",
    ]
)

# Words that say nothing about what a picture shows: they never count toward a generic
# query's overlap ("Tbilisi State University building" matched a Georgian Technical
# University photo through "university building").
GENERIC_WORDS = frozenset(
    [
        "university",
        "universities",
        "college",
        "building",
        "buildings",
        "campus",
        "logo",
        "logos",
        "emblem",
        "georgia",
        "georgian",
        "photo",
        "image",
        "picture",
        "concept",
        "view",
        "background",
    ]
)

# How a query's named entities are found (query.py parse_query()): runs of capitalized
# words ("Bank of Georgia", "OpenAI", "TSU"), joined by connectors, split at "and".
QUERY = {
    # A query asking for a mark: a logo query for an organization the script and brief
    # don't name is skipped, and the cooldown doesn't apply to an organization's own logo.
    "logo_words": ("logo", "logos", "emblem", "wordmark"),
    # Lowercase words that may sit inside a name ("Bank of Georgia", "Ministry of
    # Education").
    "connectors": ("of", "the", "de", "for", "&"),
    # Capitalized words that aren't names on their own: a run of only these is generic
    # ("English language classroom", "calendar October", "AI chat on laptop screen").
    "generic_capitalized": frozenset(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
            "ai",
            "it",
            "tv",
            "pc",
            "a4",
            "3d",
            "vr",
            "ui",
            "usb",
            "pdf",
            "cv",
            "stem",
            "diy",
            "english",
            "european",
            "french",
            "italian",
            "german",
            "spanish",
            "american",
            "british",
            "chinese",
            "japanese",
            "korean",
            "russian",
            "turkish",
            "ukrainian",
            "polish",
            "dutch",
            "swedish",
            "asian",
            "african",
            "arabic",
            "latin",
            "western",
            "eastern",
            "georgian",
        ]
    ),
    # A place name after another name only says where it is: "Varna Bulgaria" is Varna,
    # "Ministry of Education Georgia" the ministry ("Bank of Georgia" keeps it: "of").
    "trailing_places": frozenset(
        [
            "georgia",
            "bulgaria",
            "germany",
            "france",
            "italy",
            "spain",
            "poland",
            "turkey",
            "ukraine",
            "armenia",
            "azerbaijan",
            "usa",
            "uk",
            "europe",
            "tbilisi",
            "batumi",
            "kutaisi",
            "paris",
            "london",
            "berlin",
        ]
    ),
}


# Known names: each canonical name (lowercase) with the other ways a query, a file title, a script or a brief
# writes it (non-Latin ones as stems: a word matches any word it starts). A query naming one of these is a
# named-entity query even in lowercase. The channel's file (channel.entity_aliases, JSON: name -> aliases).
def _entity_aliases() -> dict[str, tuple[str, ...]]:
    path = conf().channel.entity_aliases
    if not path.is_file():
        return {}
    return {name: tuple(aliases) for name, aliases in json.loads(path.read_text(encoding="utf-8")).items()}


ENTITY_ALIASES = _entity_aliases()

# The cooldown: images shown in the last `reels` reels whose status is in `statuses`
# (newest first by creation time, not counting the reel being rendered) aren't picked
# again, except an organization's own logo when the story names that organization.
COOLDOWN = {
    "reels": 10,
    "statuses": ("approved", "published", "partially_published"),
}

# The model, temperature (0 per the spec; Google's Gemini 3 guide recommends the default 1.0 for
# reasoning-heavy work, this is a short, schema-constrained judgment), seed, thinking level (the
# lowest this model accepts, "minimal" is a 400; in reel #3 it averaged 25 thinking tokens a call)
# and media_resolution (image tokens per call are set by it, not by the pixel size: Gemini 3: 260
# low, 532 medium, 1080 high, the default; the rubric is about 480): the config's models.vision.
VISION = {
    # The image goes in as a JPEG no larger than this on its long side (or the
    # screen face's width, if that's larger); transparent images are composited on
    # this gray so dark and light logos both show.
    "max_side_px": 768,
    "jpeg_quality": 88,
    "alpha_backdrop": (128, 128, 128),
    # Bump when the prompts or schemas change; with the model and media_resolution it's
    # part of both cache keys (image facts, and relevance per paragraph). 4 (2026-10-23):
    # the quality score.
    "prompt_version": 4,
    "min_relevance": 6,
    # The image's own quality, 0-10 (modern, clean, well lit, appealing to 14-40 year
    # olds); below this it's rejected for good, like a watermark.
    "min_quality": 6,
    "tags": (3, 6),
    # An upper bound on one call's cost (a full call is about $0.0016): with a cost cap
    # (resolve.Picker cost_cap_usd) no call starts once the spend plus this would pass it.
    "call_ceiling_usd": 0.004,
}

FRAMING = {
    # Photo (cover) crop: the subject bbox grows by this fraction of its size on each
    # side, then is widened to the screen face's aspect ratio.
    "subject_pad": 0.2,
    # The crop never gets narrower than this fraction of the largest crop that fits
    # (so a tiny subject doesn't zoom the photo into mush).
    "min_crop_frac": 0.6,
    # Logo (contain) panel: a logo whose mean luminance (0..1, over opaque pixels) is
    # below the threshold is dark and gets the light panel, otherwise the dark one.
    "luminance_threshold": 0.5,
    "panel_light": "#EDEAF6",
    "panel_dark": "#1E1C45",
    # Pixels with alpha below this (0..255) are transparent for the luminance.
    "opaque_alpha": 128,
}

GENERATED = {
    # Off: a paragraph with no passing licensed image shows the idle screen. The
    # illustrations cost about $0.07 each, more than the rest of a reel's images.
    "enabled": False,
    # Queries with any of these words ask for a real mark (a brand's logo, a country's
    # flag): an illustration can only fake it, so the fallback is skipped for them.
    "skip_words": ("logo", "logos", "emblem", "wordmark", "trademark", "flag", "seal", "crest"),
    # The model (Nano Banana 2), aspect ratio, size and price: the config's models.illustrator
    # and models.prices.
    "style": (
        "Flat vector illustration, night palette: deep indigo, navy and violet with "
        "soft pink and warm yellow accents, simple bold shapes, subtle gradients. "
        "No people, no faces, no text, no letters, no numbers, no logos, "
        "not a photograph and not a depiction of a real event."
    ),
}

COMMONS = {
    "api": "https://commons.wikimedia.org/w/api.php",
    "user_agent": f"{conf().channel.robots_agent}/{__version__} ({conf().channel.contact})",
    # Serial requests with at least this gap (API:Etiquette), maxlag for the API.
    "min_interval_s": 1.0,
    "maxlag_s": 5,
    "retries": 3,
    "retry_wait_s": 5.0,
    # Download width: SVGs are rendered to PNG at this width; larger bitmaps come as
    # a thumbnail of this width (a standard Wikimedia thumbnail step).
    "download_width": 1920,
    "timeout_s": 30.0,
    "mimes": ("image/jpeg", "image/png", "image/svg+xml", "image/webp", "image/gif"),
}

PEXELS = {
    "api": "https://api.pexels.com/v1/search",
    "license": "Pexels License",
    "license_url": "https://www.pexels.com/license/",
    "orientation": "landscape",
    "locale": "en-US",
    "download_width": 1920,
    "timeout_s": 30.0,
}

PIXABAY = {
    # pixabay.com/api/docs (checked 2026-10-23): 100 requests per 60 s per key, results
    # must be cached for 24 h, no permanent hotlinking (we download to the library), no
    # systematic mass downloads, show where the images are from. largeImageURL is at most
    # 1280 px on its long side (larger sizes need full API access).
    "api": "https://pixabay.com/api/",
    "license": "Pixabay Content License",
    "license_url": "https://pixabay.com/service/license-summary/",
    "image_type": "photo",
    "safesearch": "true",
    "lang": "en",
    "large_px": 1280,
    "cache_s": 24 * 3600,
    "min_interval_s": 1.0,
    "timeout_s": 30.0,
}

# Attribution lines for attributions.txt, kept short for the reel caption.
ATTRIBUTION = {
    "author_max_chars": 40,
    **{k: v for k, v in conf().publish.credit_lines.items() if k != "sfx"},
}

CONTACT_SHEET = {
    "thumb_w": 480,
    "thumb_h": 206,
    "columns": 3,
    "label_h": 96,
    "gap": 12,
    "font_px": 16,
    # Needs Georgian glyphs for the card text; Pillow's default font is used (and
    # a note printed) if it's missing.
    "font": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "background": "#15142E",
    "pass": "#7CE08A",
    "fail": "#FF7E7E",
    "text": "#E8E6F5",
}


@dataclass(frozen=True)
class Screen:
    face_w: int
    face_h: int
    zoom_max: float  # largest Ken Burns zoom (photo mode)

    @property
    def aspect(self) -> float:
        return self.face_w / self.face_h

    @property
    def min_crop(self) -> tuple[float, float]:
        """Smallest source crop (px) that fills the face at the largest zoom without upscaling."""
        return self.face_w * self.zoom_max, self.face_h * self.zoom_max


@lru_cache(maxsize=1)
def screen() -> Screen:
    """The default wall TV (screen.json as is)."""
    return screen_from_spec(json.loads(SCREEN_JSON.read_text(encoding="utf-8")))


def screen_from_spec(spec: dict) -> Screen:
    """The wall TV of a merged screen spec (a background's sidecar over screen.json:
    reelline/media/backgrounds.py screen_spec())."""
    s = spec["screen"]
    return Screen(
        face_w=s["width"] - 2 * s["bezelPx"],
        face_h=s["height"] - 2 * s["bezelPx"],
        zoom_max=spec["image"]["kenBurns"]["zoomTo"],
    )
