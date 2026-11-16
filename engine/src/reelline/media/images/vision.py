"""Vision check of a candidate image against a paragraph (Gemini Flash).

A result has two parts, cached separately in the library:
- the image's own facts (FACT_FIELDS: watermark, text, people, logo, quality, subject
  box, tags), cached by image sha256 alone;
- its relevance to a paragraph (RELEVANCE_FIELDS), cached by (sha256, paragraph hash).
check() makes one full call (PROMPT, SCHEMA) for an image it hasn't seen, and a short
relevance-only call (RELEVANCE_PROMPT) for a known image under a new paragraph (library
reuse); both send the image as a downscaled JPEG (the vision role: models.vision). A
fresh call appends its cost row (reelline/providers/costs.py) to `calls` when given, so the render can record the
cost; `before_call` runs before each paid call (the cost cap). judge() turns a
result into pass/fail reasons:
- image-intrinsic (the image is rejected for good): watermark, heavy_text,
  graphic_content, and quality below VISION["min_quality"] (dated tech, poverty,
  dirty, sad or embarrassing scenes, amateur, blurry or low resolution);
- paragraph-specific: relevance below VISION["min_relevance"], and
  identifiable_person, which only passes when the person is the subject of the
  paragraph and the image is an official/press or encyclopedic one (source
  commons or manual), never a stock photo or a generated image.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.media.images.config import VISION, screen
from reelline.media.images.library import Library
from reelline.providers import CostLedger, LLMError, VisionChecker, get_vision

SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "integer", "minimum": 0, "maximum": 10},
        "watermark": {"type": "boolean"},
        "heavy_text": {"type": "boolean"},
        "identifiable_person": {"type": "boolean"},
        "person_is_subject": {"type": "boolean"},
        "graphic_content": {"type": "boolean"},
        "is_logo_or_graphic": {"type": "boolean"},
        "quality": {"type": "integer", "minimum": 0, "maximum": 10},
        "subject_bbox": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "minimum": 0, "maximum": 1},
                "y": {"type": "number", "minimum": 0, "maximum": 1},
                "w": {"type": "number", "minimum": 0, "maximum": 1},
                "h": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["x", "y", "w", "h"],
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": VISION["tags"][0],
            "maxItems": VISION["tags"][1],
        },
        "reason": {"type": "string"},
    },
    "required": [
        "relevance",
        "watermark",
        "heavy_text",
        "identifiable_person",
        "person_is_subject",
        "graphic_content",
        "is_logo_or_graphic",
        "quality",
        "subject_bbox",
        "tags",
        "reason",
    ],
}

# What the image is (cached per image) vs how it fits a paragraph (cached per paragraph).
FACT_FIELDS = (
    "watermark",
    "heavy_text",
    "identifiable_person",
    "graphic_content",
    "is_logo_or_graphic",
    "quality",
    "subject_bbox",
    "tags",
)
RELEVANCE_FIELDS = ("relevance", "person_is_subject", "reason")
INTRINSIC = ("watermark", "heavy_text", "graphic_content")

RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {k: SCHEMA["properties"][k] for k in RELEVANCE_FIELDS},
    "required": list(RELEVANCE_FIELDS),
}

_HEADER = """You check a candidate picture for the TV screen behind the host of a short
Georgian news video. The picture is shown while this paragraph is read.

Paragraph (Georgian): {display}
Headline card on screen: {card}
Image search query: {query}

Answer in JSON:
- relevance: 0-10, how well the picture works as the visual for this paragraph. The
  query was written to illustrate it, so judge the picture against the query in the
  context of the paragraph:
  9-10 = exactly the organization, place, product or person the paragraph names;
  7-8 = a clear, good-looking depiction of the query's subject or scene that fits the
  paragraph's topic (a generic scene or illustration counts);
  4-6 = only loosely related, or a poor or confusing depiction;
  0-3 = unrelated or misleading.
"""

PROMPT = (
    _HEADER
    + """- watermark: a stock-photo watermark or a visible credit/URL overlay.
- heavy_text: text dominates the picture (documents, screenshots, posters, slides,
  headlines). A logo's own wordmark is not heavy text.
- identifiable_person: a specific real person who could be recognized and named (a
  public figure, or a portrait where one face is the subject). Anonymous people in a
  generic scene (crowds, students, workers, backs, hands, small or blurred faces) are
  false.
- person_is_subject: identifiable_person is true and that person is the subject of
  this paragraph.
- graphic_content: violence, gore, injury, nudity or other disturbing content.
- is_logo_or_graphic: a logo, icon, wordmark, flat illustration or diagram rather
  than a photo.
- quality: 0-10, how good the picture itself looks on a modern, stylish video for
  viewers aged 14-40, whatever the paragraph:
  9-10 = modern, clean, well lit, sharp and appealing (a professional photo, or a crisp
  official logo);
  6-8 = good and current, maybe a little plain;
  3-5 = dated or amateur: old technology (CRT monitors, bulky old computers, old
  phones), a dull snapshot, cluttered framing, poor light, grainy;
  0-2 = blurry or low resolution, or a poor, dirty, run-down, sad, distressing or
  embarrassing scene.
- subject_bbox: normalized x, y, w, h (0-1, from the top-left) of the main subject.
- tags: {tag_min}-{tag_max} lowercase English tags for what the picture shows.
- reason: one short line (at most 15 words) explaining the relevance score."""
)

RELEVANCE_PROMPT = (
    _HEADER
    + """- person_is_subject: the picture shows a specific real person who could be
  recognized and named, and that person is the subject of this paragraph.
- reason: one short line (at most 15 words) explaining the relevance score."""
)


@dataclass(frozen=True)
class Judgement:
    passed: bool
    reasons: tuple[str, ...]  # why it failed; empty when passed
    intrinsic: tuple[str, ...]  # the subset that rejects the image for good


def _version_key() -> list:
    return [conf().models.vision.model, VISION["prompt_version"], conf().models.vision.media_resolution]


def facts_problems(facts: dict) -> list[str]:
    """Why the image's own facts reject it for good (empty: they don't)."""
    problems = [name for name in INTRINSIC if facts[name]]
    quality = facts.get("quality")
    if quality is not None and quality < VISION["min_quality"]:
        problems.append(f"low quality {quality} < {VISION['min_quality']}")
    return problems


def facts_key() -> str:
    """Cache key part for an image's own facts (with its sha256)."""
    return hashlib.sha256(json.dumps(_version_key()).encode()).hexdigest()


def paragraph_hash(display: str, card: str | None, query: str) -> str:
    key = [*_version_key(), display, card or "", query]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def max_side_px() -> int:
    return max(VISION["max_side_px"], screen().face_w)


def vision_jpeg(path: Path) -> bytes:
    with Image.open(path) as im:
        im.load()
        image = im.convert("RGBA")
    backdrop = Image.new("RGBA", image.size, (*VISION["alpha_backdrop"], 255))
    rgb = Image.alpha_composite(backdrop, image).convert("RGB")
    side = max_side_px()
    rgb.thumbnail((side, side))
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=VISION["jpeg_quality"])
    return buf.getvalue()


class VisionError(ReellineError, RuntimeError):
    """Raised when the vision call fails or returns something off-schema."""


def _call(client, path: Path, prompt: str, schema: dict, purpose: str, calls: list[dict] | None) -> dict:
    try:
        reply = get_vision("vision", client).look(
            vision_jpeg(path),
            prompt,
            schema,
            purpose=purpose,
            prompt_version=VISION["prompt_version"],
            ledger=CostLedger(rows=calls) if calls is not None else None,
        )
    except LLMError as exc:
        raise VisionError(f"Vision check of {path.name} failed: {exc}") from exc.__cause__
    result = reply.data
    missing = [k for k in schema["required"] if k not in result]
    if missing:
        raise VisionError(f"Vision result for {path.name} lacks {', '.join(missing)}: {result}")
    return result


def check(
    client: VisionChecker | object,
    library: Library,
    sha256: str,
    path: Path,
    display: str,
    card: str | None,
    query: str,
    calls: list[dict] | None = None,
    before_call: Callable[[], None] | None = None,
) -> tuple[dict, bool]:
    """(result, cached) for this image and paragraph; cached is True when no call was
    made. A fresh call's cost row goes to `calls`; `before_call` runs right before it
    (it may raise to stop the call). An image whose cached facts already reject it for
    good isn't judged for relevance (relevance None)."""
    fkey, phash = facts_key(), paragraph_hash(display, card, query)
    facts = library.facts_get(sha256, fkey)
    fit = library.vision_get(sha256, phash)
    if facts is not None and fit is not None:
        return {**facts, **fit}, True
    if facts is not None and facts_problems(facts):
        return {**facts, "relevance": None, "person_is_subject": False, "reason": "not judged (rejected image)"}, True
    fields = {
        "display": display,
        "card": card or "(none)",
        "query": query,
        "tag_min": VISION["tags"][0],
        "tag_max": VISION["tags"][1],
    }
    if before_call is not None:
        before_call()
    if facts is None:
        result = _call(client, path, PROMPT.format(**fields), SCHEMA, "image_vision", calls)
        result["tags"] = [t.strip().lower() for t in result["tags"] if t.strip()]
        facts = {k: result[k] for k in FACT_FIELDS}
        library.facts_put(sha256, fkey, facts)
    else:
        result = _call(client, path, RELEVANCE_PROMPT.format(**fields), RELEVANCE_SCHEMA, "image_relevance", calls)
    fit = {k: result[k] for k in RELEVANCE_FIELDS}
    # Only a person the image shows can be the subject.
    fit["person_is_subject"] = bool(fit["person_is_subject"] and facts["identifiable_person"])
    library.vision_put(sha256, phash, fit)
    return {**facts, **fit}, False


def judge(result: dict, source: str) -> Judgement:
    intrinsic = facts_problems(result)
    reasons = list(intrinsic)
    if result["relevance"] is not None and result["relevance"] < VISION["min_relevance"]:
        reasons.append(f"relevance {result['relevance']} < {VISION['min_relevance']}")
    if result["identifiable_person"] and not (result["person_is_subject"] and source in ("commons", "manual")):
        reasons.append("identifiable person" + ("" if result["person_is_subject"] else " (not the subject)"))
    return Judgement(not reasons, tuple(reasons), tuple(intrinsic))
