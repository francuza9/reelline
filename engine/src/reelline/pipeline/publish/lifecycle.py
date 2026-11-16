"""After posting: cleanup, the chat text, Retry, mark deleted, unschedule and revert."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.core.errors import PublishError
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline.publish.base import CHANNEL_ERRORS, Outcome, label, local_when, utcnow
from reelline.pipeline.publish.clients import Clients
from reelline.pipeline.publish.guard import reel_row
from reelline.pipeline.publish.queue import get_pub, publications, set_pub, set_reel
from reelline.storage.blob import StorageError


def retry_markup(pubs: list[sqlite3.Row], shared: bool = False) -> list | None:
    """The failed channels' Retry buttons (p:<publication>:rt). One 🔁 Retry all
    (p:<reel>:ra) instead when a shared step failed, and first, above them, when 2+
    channels failed each for its own reason."""
    from reelline.notify.base import buttons

    failed = [p for p in pubs if p["state"] == "failed"]
    if not failed:
        return None
    rows = []
    if shared or len(failed) > 1:
        rows.append([(conf().publish.buttons["retry_all"], f"p:{failed[0]['reel_id']}:ra")])
    if not shared:
        rows += [
            [(conf().publish.buttons["retry"].format(channel=label(p["channel"])), f"p:{p['id']}:rt")] for p in failed
        ]
    return buttons(rows)


def went_out_at(conn: sqlite3.Connection, reel_id: int) -> datetime | None:
    """When the reel first went live anywhere (its earliest live publication), else its
    published_at; None when neither is known."""
    first = conn.execute(
        """SELECT MIN(published_at) FROM publications WHERE reel_id = ? AND published_at IS NOT NULL
                            AND state IN ('published', 'deleted')""",
        (reel_id,),
    ).fetchone()[0]
    at = first or reel_row(conn, reel_id)["published_at"]
    return datetime.fromisoformat(at) if at else None


def cleanup_r2(conn: sqlite3.Connection, reel_id: int, clients: Clients, log: Callable[[str], None] = print) -> None:
    keys = json.loads(reel_row(conn, reel_id)["r2_keys"] or "[]")
    if not keys:
        return
    try:
        clients.blob.delete(keys)
    except StorageError as exc:
        log(f"Reel #{reel_id}: R2 cleanup failed ({exc}); the lifecycle rule will remove them")
        return
    set_reel(conn, reel_id, r2_keys=None)


def outcome_text(conn: sqlite3.Connection, out: Outcome, now: datetime) -> tuple[str, dict | None]:
    """The publish job's report: what's scheduled, what failed (Retry buttons). A shared
    step's failure is said once, not per channel."""
    pubs = publications(conn, out.reel_id)
    when = f"for {local_when(out.when, now)}" if out.when else "now"
    if out.shared:
        lines = [f"❌ {out.shared}", "Not posted: " + ", ".join(label(ch) for ch in out.results) + "."]
    else:
        lines = [
            f"{'✅' if state != 'failed' else '❌'} {label(ch)}" + (f": {err}" if err else "")
            for ch, (state, err) in out.results.items()
        ]
    ok = sum(1 for s, _ in out.results.values() if s != "failed")
    done = "scheduled " + when if out.when else "posted now"
    head = about_reel(out.reel_id, f"📤 {ok} of {len(out.results)} {done}")
    text = "\n".join([head] + lines + [f"⚠️ {w}" for w in out.warnings])
    return text, retry_markup([p for p in pubs if p["channel"] in out.results], shared=out.shared is not None)


def retry(conn: sqlite3.Connection, pub_id: int) -> str:
    """A failed channel back to waiting (the next tick's publish job picks it up)."""
    p = get_pub(conn, pub_id)
    if p is None:
        return "expired"
    if p["state"] != "failed":
        return f"already {p['state']}"
    set_pub(conn, pub_id, state="waiting", error=None)
    return "retrying"


def retry_all(conn: sqlite3.Connection, reel_id: int) -> str:
    """Every failed channel of the reel back to waiting: one publish job then takes them
    with any waiting one, the shared steps once. Scheduled and published channels stay
    as they are (their stored external ids keep a second post from being made)."""
    pubs = publications(conn, reel_id)
    if not pubs:
        return "expired"
    failed = [p for p in pubs if p["state"] == "failed"]
    if not failed and not any(p["state"] == "waiting" for p in pubs):
        return "nothing to retry"
    for p in failed:
        set_pub(conn, p["id"], state="waiting", error=None)
    return "retrying"


def mark_deleted(
    conn: sqlite3.Connection, reel_id: int, channel: str, now: datetime | None = None, how: str = "API"
) -> str:
    """A live post taken down (`how`: through the API, or by hand); the reel becomes
    deleted once every channel it went out on is. Returns the reel's status."""
    p = next((x for x in publications(conn, reel_id) if x["channel"] == channel), None)
    if p is None:
        raise PublishError(f"Reel #{reel_id} has no {label(channel)} publication")
    if p["state"] not in ("published", "deleted"):
        raise PublishError(f"Reel #{reel_id} on {label(channel)} is {p['state']}, not published")
    set_pub(conn, p["id"], now, state="deleted", error=f"deleted {how} {db.now_iso(now)}")
    live = [x for x in publications(conn, reel_id) if x["state"] not in ("unscheduled", "waiting")]
    if live and all(x["state"] == "deleted" for x in live):
        set_reel(conn, reel_id, now, status="deleted")
    return reel_row(conn, reel_id)["status"]


def can_unschedule(conn: sqlite3.Connection, reel: sqlite3.Row, now: datetime) -> str | None:
    """None if Unschedule is allowed now, else why not."""
    if reel["status"] != "approved":
        return f"reel is {reel['status']}"
    slot = datetime.fromisoformat(reel["publish_at"]) if reel["publish_at"] else None
    if reel["publish_mode"] == "now" or slot is None:
        return "posting now"
    if now >= slot - timedelta(minutes=conf().publish.unschedule_until_min):
        return f"too late (until {conf().publish.unschedule_until_min} min before the slot)"
    if any(p["state"] in ("published",) for p in publications(conn, reel["id"])):
        return "already live somewhere"
    return None


def needs_network(conn: sqlite3.Connection, reel_id: int) -> bool:
    """Does unscheduling have anything to delete (a post or an R2 object)?"""
    reel = reel_row(conn, reel_id)
    return bool(json.loads(reel["r2_keys"] or "[]")) or any(
        p["external_id"] for p in publications(conn, reel_id) if p["state"] != "unscheduled"
    )


def unschedule(
    conn: sqlite3.Connection,
    reel_id: int,
    clients: Clients | None,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Delete every created post and the R2 objects, then the reel back to rendered.
    Returns the problems (non-empty: nothing reverted, the reel stays approved)."""
    now = now or utcnow()
    errors = []
    for p in publications(conn, reel_id):
        if p["state"] == "unscheduled" or not p["external_id"]:
            continue
        try:
            clients.publishers[p["channel"]].delete(p["channel"], p["external_id"])
        except CHANNEL_ERRORS as exc:
            errors.append(f"{label(p['channel'])}: {exc}")
            continue
        set_pub(conn, p["id"], state="unscheduled", external_id=None)
    if errors:
        return errors
    with db.transaction(conn):
        conn.execute(
            "UPDATE publications SET state = 'unscheduled', updated_at = ? WHERE reel_id = ?",
            (db.now_iso(now), reel_id),
        )
    keys = json.loads(reel_row(conn, reel_id)["r2_keys"] or "[]")
    if keys:
        try:
            clients.blob.delete(keys)
            set_reel(conn, reel_id, r2_keys=None)
        except StorageError as exc:
            log(f"Reel #{reel_id}: R2 delete failed ({exc}); the lifecycle rule will remove them")
    revert(conn, reel_id, now)
    return []


def revert(conn: sqlite3.Connection, reel_id: int, now: datetime | None = None) -> None:
    """The reel back to rendered (its pick and script too), as before the approval."""
    reel = reel_row(conn, reel_id)
    script = conn.execute("SELECT failures FROM scripts WHERE id = ?", (reel["script_id"],)).fetchone()
    at = db.now_iso(now)
    with db.transaction(conn):
        conn.execute(
            """UPDATE reels SET status = 'rendered', approved_at = NULL, approved_by = NULL,
                            publish_at = NULL, publish_mode = NULL, updated_at = ? WHERE id = ?""",
            (at, reel_id),
        )
        db.update_pick(conn, reel["pick_id"], status="rendered")
        conn.execute(
            "UPDATE scripts SET status = ?, updated_at = ? WHERE id = ?",
            ("flagged" if json.loads(script["failures"] or "[]") else "ok", at, reel["script_id"]),
        )
