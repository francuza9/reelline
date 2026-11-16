"""Image library: data/image_library/ (gitignored).

Files are stored as files/<id>.<ext>, id = the first IMAGE_ID_LEN hex digits of the
file's sha256 (what `@image: lib:<id>` pins). library.sqlite (stdlib sqlite3) has:

- images: one row per candidate ever evaluated, accepted or rejected, keyed by id and
  by source_key (e.g. "commons:File:X.svg", "pixabay:123"), so a rejected candidate
  is never fetched or evaluated again. A candidate rejected before download (license,
  size) has no file; its id is derived from the source key instead.
  status "accepted" = licensed and passed every image-intrinsic check (resolution,
  animation, watermark, heavy text, graphic content, quality); relevance and the
  person check depend on the paragraph and live in vision_cache. queries: the queries
  it passed for. subjects: what its source says it shows (a Commons file's categories,
  a manual add's tags; the title comes from source_key), the only text a named-entity
  query is matched against (subject_names()).
- image_facts: an image's own vision facts (watermark, text, people, logo, subject
  box, tags) per (sha256, facts key = model + prompt version + media resolution).
- vision_cache: relevance to a paragraph per (image sha256, paragraph hash).
- search_cache: a source's search response per request (Pixabay asks for 24 h caching).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from reelline.media.images.config import LIBRARY, LIBRARY_DIR, STOPWORDS
from reelline.media.images.query import Query, content_words, names_entity, parse_query
from reelline.media.script_markup import IMAGE_ID_LEN

_JSON_COLUMNS = ("queries", "tags", "vision", "subjects")
_BOOL_COLUMNS = ("attribution_required", "editorial_only", "is_logo_or_graphic")

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    sha256 TEXT,
    path TEXT,
    width INTEGER,
    height INTEGER,
    mime TEXT,
    source TEXT NOT NULL,
    source_key TEXT UNIQUE,
    page_url TEXT,
    file_url TEXT,
    license TEXT,
    license_url TEXT,
    author TEXT,
    author_url TEXT,
    attribution TEXT,
    attribution_required INTEGER,
    editorial_only INTEGER,
    fetched_at TEXT NOT NULL,
    queries TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    vision TEXT,
    is_logo_or_graphic INTEGER,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')),
    reason TEXT,
    subjects TEXT
);
CREATE TABLE IF NOT EXISTS image_facts (
    sha256 TEXT NOT NULL,
    facts_key TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sha256, facts_key)
);
CREATE TABLE IF NOT EXISTS vision_cache (
    sha256 TEXT NOT NULL,
    paragraph_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sha256, paragraph_hash)
);
CREATE TABLE IF NOT EXISTS search_cache (
    source TEXT NOT NULL,
    request TEXT NOT NULL,
    body TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, request)
);
"""

# Sources whose files can stand for a named entity: an encyclopedic file that names it,
# or one added by hand. Never a stock photo or an illustration.
NAMED_SOURCES = ("commons", "manual")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def image_id(sha256: str) -> str:
    return sha256[:IMAGE_ID_LEN]


def key_id(source_key: str) -> str:
    """Id for a candidate rejected before download (no file, no sha256)."""
    return hashlib.sha256(source_key.encode()).hexdigest()[:IMAGE_ID_LEN]


def query_words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOPWORDS]


def normalize_query(query: str) -> str:
    return " ".join(query_words(query))


def is_logo(record: dict) -> bool:
    """A mark (a logo or wordmark), not a photo, an illustration or a diagram."""
    return bool(record.get("is_logo_or_graphic") and record.get("editorial_only"))


def subject_names(record: dict) -> list[str]:
    """What the image's source says it shows: a Commons file's title (from source_key)
    and categories, a manual add's tags. Vision tags don't count (the model guesses)."""
    names = list(record.get("subjects") or [])
    key = record.get("source_key") or ""
    if key.startswith("commons:"):
        names.append(Path(key.split(":", 1)[1].removeprefix("File:")).stem)
    if record.get("source") == "manual" and not record.get("subjects"):
        names += record.get("tags") or []
    return names


class Library:
    def __init__(self, root: Path = LIBRARY_DIR):
        self.root = root
        self.files = root / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "library.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(images)")}
        if "subjects" not in columns:  # libraries from before 2026-10-23
            with self.db:
                self.db.execute("ALTER TABLE images ADD COLUMN subjects TEXT")

    def close(self) -> None:
        self.db.close()

    # --- rows -----------------------------------------------------------------------

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        for col in _JSON_COLUMNS:
            out[col] = json.loads(out[col]) if out[col] is not None else None
        for col in _BOOL_COLUMNS:
            out[col] = None if out[col] is None else bool(out[col])
        return out

    def get(self, image_id_: str) -> dict | None:
        return self._decode(self.db.execute("SELECT * FROM images WHERE id = ?", (image_id_,)).fetchone())

    def by_source_key(self, source_key: str) -> dict | None:
        return self._decode(self.db.execute("SELECT * FROM images WHERE source_key = ?", (source_key,)).fetchone())

    def put(self, record: dict) -> dict:
        """Insert or replace a row (by id); JSON/bool columns are encoded here."""
        row = dict(record)
        row.setdefault("fetched_at", now())
        for col in _JSON_COLUMNS:
            if col in row and row[col] is not None:
                row[col] = json.dumps(row[col], ensure_ascii=False)
        for col in _BOOL_COLUMNS:
            if col in row and row[col] is not None:
                row[col] = int(bool(row[col]))
        cols = list(row)
        with self.db:
            self.db.execute(
                f"INSERT OR REPLACE INTO images ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [row[c] for c in cols],
            )
        return self.get(row["id"])

    def update(self, image_id_: str, **fields) -> dict:
        record = self.get(image_id_)
        if record is None:
            raise KeyError(f"No library image {image_id_}")
        record.update(fields)
        return self.put(record)

    def add_query(self, image_id_: str, query: str) -> None:
        record = self.get(image_id_)
        if record and query not in record["queries"]:
            self.update(image_id_, queries=[*record["queries"], query])

    def all(self, status: str | None = None, tag: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM images", []
        if status:
            sql, args = sql + " WHERE status = ?", [status]
        rows = [self._decode(r) for r in self.db.execute(sql + " ORDER BY fetched_at, id", args)]
        if tag:
            rows = [r for r in rows if tag.lower() in (r["tags"] or [])]
        return rows

    # --- files ----------------------------------------------------------------------

    def store_file(self, data: bytes, ext: str) -> tuple[str, Path]:
        """(sha256, path) of the stored bytes (content-addressed; idempotent)."""
        sha = hashlib.sha256(data).hexdigest()
        path = self.files / f"{image_id(sha)}.{ext}"
        if not path.is_file():
            path.write_bytes(data)
        return sha, path

    def path(self, record: dict) -> Path:
        if not record.get("path"):
            raise FileNotFoundError(f"Library image {record['id']} has no file (rejected before download)")
        path = self.root / record["path"]
        if not path.is_file():
            raise FileNotFoundError(f"Library image {record['id']}: file missing: {path}")
        return path

    # --- matching -------------------------------------------------------------------

    def matches(self, query: str | Query) -> list[tuple[int, dict]]:
        """Accepted images matching the query, best first, as (score, record).

        A named-entity query (query.py) matches only an image from NAMED_SOURCES whose
        source names one of its entities (subject_names(), names_entity()); a logo query
        only a logo or graphic, any other named query no logo. Scored by the words it
        shares with the image's tags.

        A generic query never matches a logo. It matches an equal normalized past query
        (one the image passed for), or LIBRARY["min_overlap"] content words (no
        STOPWORDS or GENERIC_WORDS) in the image's vision tags, all of them if the query
        has fewer."""
        q = query if isinstance(query, Query) else parse_query(query)
        norm = normalize_query(q.text)
        words = content_words(q.text)
        need = min(LIBRARY["min_overlap"], len(words))
        out = []
        for record in self.all(status="accepted"):
            if not record.get("path"):
                continue
            vocab = {w for t in record["tags"] or [] for w in content_words(t)}
            if q.named:
                if record["source"] not in NAMED_SOURCES:
                    continue
                if (not record["is_logo_or_graphic"]) if q.logo else is_logo(record):
                    continue
                names = " | ".join(subject_names(record))
                if any(names_entity(names, e) for e in q.entities):
                    out.append((1 + len(words & vocab), record))
                continue
            if is_logo(record):
                continue
            if norm in {normalize_query(past) for past in record["queries"]}:
                out.append((len(words) + 1, record))
                continue
            overlap = len(words & vocab)
            if need and overlap >= need:
                out.append((overlap, record))
        out.sort(key=lambda pair: (-pair[0], pair[1]["fetched_at"], pair[1]["id"]))
        return out

    # --- vision cache ---------------------------------------------------------------

    def vision_get(self, sha256: str, paragraph_hash: str) -> dict | None:
        row = self.db.execute(
            "SELECT result FROM vision_cache WHERE sha256 = ? AND paragraph_hash = ?", (sha256, paragraph_hash)
        ).fetchone()
        return json.loads(row["result"]) if row else None

    def vision_put(self, sha256: str, paragraph_hash: str, result: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO vision_cache VALUES (?, ?, ?, ?)",
                (sha256, paragraph_hash, json.dumps(result, ensure_ascii=False), now()),
            )

    def facts_get(self, sha256: str, facts_key: str) -> dict | None:
        row = self.db.execute(
            "SELECT result FROM image_facts WHERE sha256 = ? AND facts_key = ?", (sha256, facts_key)
        ).fetchone()
        return json.loads(row["result"]) if row else None

    def search_get(self, source: str, request: str, max_age_s: float) -> dict | None:
        """A cached search response no older than max_age_s."""
        row = self.db.execute(
            "SELECT body, fetched_at FROM search_cache WHERE source = ? AND request = ?", (source, request)
        ).fetchone()
        if row is None:
            return None
        age = (datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
        return json.loads(row["body"]) if age <= max_age_s else None

    def search_put(self, source: str, request: str, body: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO search_cache VALUES (?, ?, ?, ?)",
                (source, request, json.dumps(body, ensure_ascii=False), now()),
            )

    def facts_put(self, sha256: str, facts_key: str, result: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO image_facts VALUES (?, ?, ?, ?)",
                (sha256, facts_key, json.dumps(result, ensure_ascii=False), now()),
            )
