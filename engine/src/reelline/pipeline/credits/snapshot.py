"""The credits snapshot: each shown image's title, author and license, kept per reel."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from reelline.config import conf
from reelline.paths import stored_path
from reelline.pipeline import db
from reelline.pipeline.credits.base import SOURCE_NAMES, CreditsError


def image_title(entry: dict, record: dict | None) -> str:
    key = (record or {}).get("source_key") or ""
    if key.startswith("commons:"):
        name = key.split(":", 1)[1].removeprefix("File:")
        return Path(name).stem.replace("_", " ").strip()
    url = entry.get("page_url") or (record or {}).get("page_url") or ""
    m = re.search(r"pexels\.com/photo/([a-z0-9-]+?)-?(\d+)/?$", url) or re.search(
        r"pixabay\.com/(?:[a-z]{2}/)?photos/([a-z0-9-]+?)-?(\d+)/?$", url
    )
    if m and m.group(1):
        return m.group(1).replace("-", " ").strip().capitalize()
    if entry.get("source") == "generated":
        return conf().publish.credits_page.labels["generated"]
    return entry.get("value") or entry.get("image_id") or conf().publish.credits_page.labels["unknown"]


def commons_author(source_key: str, fetch_json: Callable[[dict], dict] | None = None) -> str | None:
    """The file's full Artist from the Commons API (the library keeps the shortened
    caption form); None when it can't be read."""
    from reelline.media.images.licenses import strip_html

    if fetch_json is None:
        import httpx

        from reelline.media.images.sources import USER_AGENT

        def fetch_json(params):
            r = httpx.get(
                "https://commons.wikimedia.org/w/api.php", params=params, timeout=30, headers={"User-Agent": USER_AGENT}
            )
            r.raise_for_status()
            return r.json()

    title = "File:" + source_key.split(":", 1)[1].removeprefix("File:")
    try:
        data = fetch_json(
            {
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "extmetadata",
                "format": "json",
                "formatversion": "2",
            }
        )
        meta = data["query"]["pages"][0]["imageinfo"][0]["extmetadata"]
        return strip_html(meta["Artist"]["value"]).strip() or None
    except Exception:  # network, missing fields: keep the short form
        return None


def snapshot_images(images_json: Path, library=None, fetch_json: Callable[[dict], dict] | None = None) -> list[dict]:
    """The reel's shown images (each once, in paragraph order) with what the page needs.
    A shortened Commons author ("…") is fetched in full from the Commons API."""
    if library is None:
        from reelline.media.images.library import Library

        library = Library()
    manifest = json.loads(images_json.read_text(encoding="utf-8"))
    out, seen = [], set()
    for entry in manifest.get("paragraphs", []):
        if entry.get("status") != "image" or not entry.get("image_id") or entry["image_id"] in seen:
            continue
        seen.add(entry["image_id"])
        record = library.get(entry["image_id"]) if library else None
        author = (record or {}).get("author") or entry.get("author")
        if not author and entry.get("source") == "generated":
            author = conf().publish.credits_page.labels["generated_author"]
        key = (record or {}).get("source_key") or ""
        if author and author.endswith("…") and key.startswith("commons:"):
            author = commons_author(key, fetch_json) or author
        out.append(
            {
                "image_id": entry["image_id"],
                "source": entry.get("source"),
                "source_name": SOURCE_NAMES.get(entry.get("source"), entry.get("source") or ""),
                "title": image_title(entry, record),
                "author": author,
                "author_url": (record or {}).get("author_url"),
                "license": entry.get("license"),
                "license_url": entry.get("license_url"),
                "page_url": entry.get("page_url") or (record or {}).get("page_url"),
                "attribution_required": bool(entry.get("attribution_required")),
            }
        )
    return out


def snapshot(
    conn: sqlite3.Connection, reel_id: int, library=None, fetch_json: Callable[[dict], dict] | None = None
) -> list[dict]:
    """The reel's credits: the stored snapshot, or one made now from its images.json."""
    row = conn.execute("SELECT images FROM credits WHERE reel_id = ?", (reel_id,)).fetchone()
    if row is not None:
        return json.loads(row["images"])
    reel = conn.execute("SELECT images_path FROM reels WHERE id = ?", (reel_id,)).fetchone()
    if reel is None or not reel["images_path"] or not stored_path(reel["images_path"]).is_file():
        raise CreditsError(f"Reel #{reel_id} has no images.json to take its credits from")
    images = snapshot_images(stored_path(reel["images_path"]), library, fetch_json)
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO credits (reel_id, images, created_at) VALUES (?, ?, ?)",
            (reel_id, json.dumps(images, ensure_ascii=False), db.now_iso()),
        )
    return images
