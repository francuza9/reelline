"""The database schema text is byte-identical across refactors: every migration (version, name, statements, rebuild)
and the schema a fresh database ends up with are compared with tests/snapshots/db_schema.json. Regenerate on purpose
with UPDATE_SNAPSHOTS=1 (and say why in the commit)."""

import json
import os
from pathlib import Path

from reelline.pipeline import db

SNAP = Path(__file__).resolve().parent / "snapshots" / "db_schema.json"


def _current(tmp_path) -> dict:
    conn = db.connect(tmp_path / "schema.db")
    try:
        objects = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        versions = [r["version"] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]
    finally:
        conn.close()
    return {
        "migrations": [
            {"version": m.version, "name": m.name, "statements": list(m.statements), "rebuild": m.rebuild}
            for m in db.MIGRATIONS
        ],
        "objects": [[r["type"], r["name"], r["sql"]] for r in objects],
        "applied": versions,
    }


def test_schema_is_byte_identical(tmp_path):
    got = _current(tmp_path)
    if os.environ.get("UPDATE_SNAPSHOTS") or not SNAP.exists():
        SNAP.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
    want = json.loads(SNAP.read_text(encoding="utf-8"))
    assert got["applied"] == want["applied"]
    assert got["migrations"] == want["migrations"]
    assert got["objects"] == want["objects"]
