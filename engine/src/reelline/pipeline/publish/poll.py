"""Polling scheduled posts until they are live, and finalizing the reel."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline.publish.base import CHANNEL_ERRORS, label
from reelline.pipeline.publish.clients import Clients
from reelline.pipeline.publish.guard import reel_row
from reelline.pipeline.publish.lifecycle import cleanup_r2, retry_markup
from reelline.pipeline.publish.queue import get_pub, publications, set_pub, set_reel


def _comment_state(channel: str) -> str | None:
    """Instagram's comment is Buffer's own first comment with
    publish.instagram_first_comment; every other comment is decided when the post
    goes live (reelline/pipeline/comments.py went_live())."""
    return "buffer" if channel == "instagram" and conf().publish.instagram_first_comment else None


def poll(
    conn: sqlite3.Connection,
    clients_for: Callable[[int], Clients],
    now: datetime,
    send: Callable[[str, dict | None], object],
    log: Callable[[str], None] = print,
    interval: timedelta | None = None,
) -> None:
    """Check the scheduled posts whose time has come (each every poll_interval_min),
    record what went live, post the links comments (and the pending ones), and report
    reels that are done."""
    interval = interval if interval is not None else timedelta(minutes=conf().publish.poll_interval_min)
    rows = conn.execute(
        """SELECT * FROM publications WHERE state = 'scheduled' AND due_at <= ?
                           ORDER BY reel_id, id""",
        (db.now_iso(now),),
    ).fetchall()
    touched = set()
    for p in rows:
        if p["checked_at"] and datetime.fromisoformat(p["checked_at"]) > now - interval:
            continue
        clients = clients_for(p["reel_id"])
        try:
            check(conn, p, clients, now, send, log)
        except CHANNEL_ERRORS as exc:  # can't reach the API now: try again next round
            log(f"Reel #{p['reel_id']}: {label(p['channel'])} status check failed: {exc}")
            set_pub(conn, p["id"], checked_at=db.now_iso(now))
        touched.add(p["reel_id"])
    from reelline.pipeline import comments

    comments.run_pending(conn, clients_for, send, log, now)
    for reel_id in sorted(touched):
        finalize(conn, reel_id, clients_for(reel_id), send, log, now)


def check(
    conn: sqlite3.Connection,
    p: sqlite3.Row,
    clients: Clients,
    now: datetime,
    send: Callable[[str, dict | None], object],
    log: Callable[[str], None],
) -> str:
    """One scheduled post's status now; returns its state."""
    ch, timeout = p["channel"], timedelta(hours=conf().publish.poll_timeout_h)
    from reelline.pipeline import comments

    late = now - datetime.fromisoformat(p["due_at"]) > timeout
    stamp = db.now_iso(now)
    st = clients.publishers[ch].status(ch, p["external_id"])
    if st.state == "published":
        set_pub(conn, p["id"], state="published", url=st.url, published_at=stamp, checked_at=stamp)
        comments.went_live(conn, get_pub(conn, p["id"]), clients, send, log, now)
        return "published"
    if st.state in ("failed", "gone"):
        set_pub(conn, p["id"], state="failed", error=st.error or "error", checked_at=stamp)
        return "failed"
    state = st.detail
    if late:
        set_pub(
            conn,
            p["id"],
            state="failed",
            checked_at=stamp,
            error=f"not live {conf().publish.poll_timeout_h} h after its time (status {state})",
        )
        return "failed"
    set_pub(conn, p["id"], checked_at=stamp)
    return "scheduled"


def finalize(
    conn: sqlite3.Connection,
    reel_id: int,
    clients: Clients,
    send: Callable[[str, dict | None], object],
    log: Callable[[str], None] = print,
    now: datetime | None = None,
) -> str | None:
    """Every enabled channel done: published (pick posted, the links, R2 cleaned) or
    partially_published (Retry buttons). Says so only when the reel's status changes."""
    reel = reel_row(conn, reel_id)
    pubs = [p for p in publications(conn, reel_id) if p["state"] not in ("unscheduled", "deleted")]
    if not pubs or any(p["state"] not in ("published", "failed") for p in pubs):
        return None
    status = "published" if all(p["state"] == "published" for p in pubs) else "partially_published"
    if status == reel["status"]:
        return status
    lines = [
        f"{'✅' if p['state'] == 'published' else '❌'} {label(p['channel'])}: "
        + (p["url"] or "live (no link from the API)" if p["state"] == "published" else p["error"] or "failed")
        for p in pubs
    ]
    if status == "published":
        set_reel(conn, reel_id, now, status="published", published_at=db.now_iso(now))
        db.update_pick(conn, reel["pick_id"], status="posted")
        send(about_reel(reel_id, f"🎉 Live on all {len(pubs)}:\n" + "\n".join(lines)), None)
        cleanup_r2(conn, reel_id, clients, log)
    else:
        set_reel(conn, reel_id, now, status="partially_published")
        send(about_reel(reel_id, "⚠️ Partially published:\n" + "\n".join(lines)), retry_markup(pubs))
    log(f"Reel #{reel_id}: {status}")
    return status
