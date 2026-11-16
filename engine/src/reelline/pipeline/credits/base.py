"""The credits rule: caption line, page address, the images a reel shows."""

from __future__ import annotations

import json
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import ReellineError

SOURCE_NAMES = {
    "commons": "Wikimedia Commons",
    "pixabay": "Pixabay",
    "pexels": "Pexels",
    "generated": "Gemini (AI)",
    "manual": conf().publish.credits_page.labels["manual"],
}


class CreditsError(ReellineError, RuntimeError):
    """The credits page couldn't be built, deployed or reached."""


def caption_line(reel_id: int | None) -> str:
    """The caption's one credits line (a preview without a reel shows "…")."""
    return conf().publish.credits_page.line.format(
        domain=conf().publish.credits_page.domain, reel_id=reel_id if reel_id is not None else "…"
    )


def page_url(reel_id: int) -> str:
    return f"https://{conf().publish.credits_page.domain}/c/{reel_id}"


def shown_images(images_json: Path | None) -> list[str]:
    """The ids of the images the reel shows on the TV, each once, in paragraph order;
    none without an images.json (a --no-images render removes it). An unreadable one
    raises CreditsError: which images went on screen can't be told."""
    if images_json is None or not images_json.is_file():
        return []
    try:
        manifest = json.loads(images_json.read_text(encoding="utf-8"))
        entries = manifest["paragraphs"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CreditsError(f"{images_json}: unreadable ({type(exc).__name__}: {exc})") from None
    out: list[str] = []
    for entry in entries:
        if entry.get("status") == "image":
            if not entry.get("image_id"):
                raise CreditsError(f"{images_json}: paragraph {entry.get('paragraph')} shows an image without an id")
            if entry["image_id"] not in out:
                out.append(entry["image_id"])
    return out


def caption_problems(caption: str | None, reel_id: int | None, shown: list[str]) -> list[str]:
    """What breaks the credits rule in `caption` (empty: fine): with images shown, this
    reel's line must be there, on caption line publish.credits_page.max_line or above (where
    Instagram's and TikTok's "...more" has it); without, no line. A preview (no reel id)
    checks the "…" form."""
    line = caption_line(reel_id)
    prefix = conf().publish.credits_page.line.split("{", 1)[0]
    lines = (caption or "").splitlines()
    problems = []
    if shown:
        if line not in lines:
            problems.append(f"the reel shows {len(shown)} image(s) but the caption lacks {line!r}")
        elif (at := lines.index(line) + 1) > conf().publish.credits_page.max_line:
            problems.append(
                f"{line!r} is on caption line {at}; it must be on line {conf().publish.credits_page.max_line} or "
                "above (a one-line hook)"
            )
    elif line in lines:
        problems.append(f"the caption has {line!r} but the reel shows no images (there's no credits page)")
    other = [x for x in lines if x.startswith(prefix) and x != line]
    if other:
        problems.append(f"a credits line that isn't this reel's: {other[0]!r}")
    return problems
