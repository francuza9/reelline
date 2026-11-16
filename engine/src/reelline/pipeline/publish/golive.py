"""Go live and repost for a reel that is already published or scheduled."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import PublishError
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.pipeline.publish.base import enabled_channels, label, utcnow
from reelline.pipeline.publish.clients import Clients
from reelline.pipeline.publish.flow import publish_reel, upload_and_presign
from reelline.pipeline.publish.guard import guard, reel_row
from reelline.pipeline.publish.lifecycle import cleanup_r2, mark_deleted, outcome_text
from reelline.pipeline.publish.livetest import curl_check, redact, url_checks
from reelline.pipeline.publish.poll import poll
from reelline.pipeline.publish.queue import enqueue, publications, set_pub, set_reel
from reelline.pipeline.publish.verify import http_text, verify
from reelline.publish.meta import FacebookError


def go_live(
    conn: sqlite3.Connection,
    reel_id: int,
    clients: Clients,
    *,
    send: Callable[[str, dict | None], object],
    log: Callable[[str], None] = print,
    clock: Callable[[], datetime] = utcnow,
    sleep: Callable[[float], None] = time.sleep,
    probe=None,
    curl: Callable[[str], dict] = curl_check,
    fetch: Callable[[str], str] = http_text,
    out_dir: Path | None = None,
    delete_facebook: bool = True,
) -> dict:
    """Publish an approved reel NOW, publicly, on every enabled channel through the real
    flow (enqueue, publish_reel, poll, finalize); verify each post; delete the Facebook
    reel (the others are deleted by hand). publish.enabled is on for this run only."""
    reel = reel_row(conn, reel_id)
    out_dir = out_dir or conf().storage.publish_test_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    start = clock()
    path = out_dir / f"live_reel_{reel_id}_{start.strftime('%Y%m%dT%H%M%SZ')}.json"
    report: dict = {"reel_id": reel_id, "started_at": db.now_iso(start), "steps": [], "ok": False}

    def save():
        path.write_text(json.dumps(redact(report), ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    def step(name, ok, detail=None):
        report["steps"].append({"step": name, "ok": ok, "detail": detail})
        log(f"{'✅' if ok else '❌'} {name}" + (f": {detail}" if detail is not None and not ok else ""))
        save()
        return ok

    if not step("reel is approved", reel["status"] == "approved", reel["status"]):
        return report
    problems = guard(conn, reel, enabled_channels(), probe)
    if not step("guard", not problems, problems or None):
        return report
    was = conf().publish.enabled
    conf().publish.enabled = True
    try:
        now = clock()
        set_reel(conn, reel_id, now, publish_mode="now", publish_at=db.now_iso(now))
        enqueue(conn, reel_id, send=lambda text: send(text, None), log=log, now=now, probe=probe)
        reel = reel_row(conn, reel_id)
        urls = upload_and_presign(conn, reel, clients, now, None)
        report["r2_keys"] = json.loads(reel_row(conn, reel_id)["r2_keys"])
        report["r2_key"] = report["r2_keys"][0]
        distinct = {u.public: u for u in urls.values()}
        if not all(
            [
                url_checks(
                    curl,
                    u.presigned,
                    u.public,
                    report.setdefault("url_checks", {}).setdefault(public, {}),
                    step,
                    public.rsplit("/", 1)[1] if len(distinct) > 1 else "",
                )
                for public, u in distinct.items()
            ]
        ):
            return report
        out = publish_reel(conn, reel_id, clients, now=clock(), log=log, probe=probe)
        text, markup = outcome_text(conn, out, clock())
        send(text, markup)
        report["posted"] = {ch: {"state": s, "error": e} for ch, (s, e) in out.results.items()}
        step("posted on every channel", all(s != "failed" for s, _ in out.results.values()), report["posted"])
        deadline = clock() + timedelta(hours=conf().publish.poll_timeout_h, minutes=10)
        while clock() < deadline:
            poll(conn, lambda r: clients, clock(), send, log, interval=timedelta(0))
            if reel_row(conn, reel_id)["status"] in ("published", "partially_published"):
                break
            sleep(conf().publish.live_poll_s)
        final = reel_row(conn, reel_id)
        report["live"] = {
            p["channel"]: {"state": p["state"], "url": p["url"], "error": p["error"], "comment": p["comment_state"]}
            for p in publications(conn, reel_id)
        }
        step("live on every channel", final["status"] == "published", report["live"])
        step(
            "R2 objects deleted after all four went live",
            not json.loads(final["r2_keys"] or "[]")
            and not clients.blob.list(report["r2_key"].rsplit("/", 1)[0] + "/"),
            final["r2_keys"],
        )
        report["verify"] = verify(conn, reel_id, clients, fetch)
        diffs = {ch: v["diffs"] for ch, v in report["verify"].items() if v["diffs"]}
        step("every post matches what we sent", not diffs, diffs or None)
        report["unverified"] = {ch: v["unverified"] for ch, v in report["verify"].items() if v["unverified"]}
        if delete_facebook:
            fb_pub = next((p for p in publications(conn, reel_id) if p["channel"] == "facebook"), None)
            if fb_pub and fb_pub["external_id"]:
                try:
                    clients.publishers["facebook"].delete("facebook", fb_pub["external_id"])
                    gone = not clients.publishers["facebook"].exists("facebook", fb_pub["external_id"])
                    if gone:
                        mark_deleted(conn, reel_id, "facebook", clock())
                    step("Facebook reel deleted (verified gone)", gone)
                except FacebookError as exc:
                    step("Facebook reel deleted", False, f"{exc} - DELETE IT BY HAND")
    finally:
        conf().publish.enabled = was
        report["ok"] = all(x["ok"] for x in report["steps"])
        report["finished_at"] = db.now_iso(clock())
        save()
        log(f"Report: {path}")
    return redact(report)


def repost(
    conn: sqlite3.Connection,
    reel_id: int,
    channels: list[str],
    clients: Clients,
    *,
    source_names: dict[str, str],
    send: Callable[[str, dict | None], object],
    log: Callable[[str], None] = print,
    clock: Callable[[], datetime] = utcnow,
    sleep: Callable[[float], None] = time.sleep,
    probe=None,
) -> dict:
    """Post an already published (or taken down) reel again, now, on `channels` only:
    the caption reassembled (today's format: the credits page line), those channels'
    publications reset (deleted or failed only), posted through publish_reel(), polled
    until live (links comment included), then the R2 object removed. Returns
    {channel: (state, url or error)}."""
    from reelline.pipeline.caption import assemble_caption

    reel = reel_row(conn, reel_id)
    if reel["status"] not in ("published", "partially_published", "deleted"):
        raise PublishError(f"Reel #{reel_id} is {reel['status']}: repost is for reels that went out already")
    pubs = {p["channel"]: p for p in publications(conn, reel_id)}
    for ch in channels:
        if ch not in pubs or pubs[ch]["state"] not in ("deleted", "failed"):
            raise PublishError(
                f"Reel #{reel_id} on {label(ch)} is {pubs[ch]['state'] if ch in pubs else 'missing'}: "
                "only a deleted or failed channel can be reposted"
            )
    now = clock()
    caption = assemble_caption(conn, reel["script_id"], source_names, reel_id)
    set_reel(conn, reel_id, now, caption=caption, publish_mode="now", publish_at=db.now_iso(now))
    for ch in channels:
        set_pub(
            conn,
            pubs[ch]["id"],
            now,
            state="waiting",
            external_id=None,
            url=None,
            error=None,
            comment_state=None,
            comment_id=None,
            comment_error=None,
            comment_at=None,
            published_at=None,
            checked_at=None,
        )
    out = publish_reel(
        conn,
        reel_id,
        clients,
        channels=channels,
        now=now,
        log=log,
        probe=probe,
        statuses=("published", "partially_published", "deleted"),
    )
    failed = {ch: e for ch, (s, e) in out.results.items() if s == "failed"}
    if not failed:
        deadline = clock() + timedelta(hours=conf().publish.poll_timeout_h, minutes=10)
        while clock() < deadline:
            poll(conn, lambda r: clients, clock(), send, log, interval=timedelta(0))
            states = {p["channel"]: p["state"] for p in publications(conn, reel_id) if p["channel"] in channels}
            if all(st in ("published", "failed") for st in states.values()):
                break
            sleep(conf().publish.live_poll_s)
    result = {
        p["channel"]: (p["state"], p["url"] if p["state"] == "published" else p["error"])
        for p in publications(conn, reel_id)
        if p["channel"] in channels
    }
    if all(st == "published" for st, _ in result.values()):
        cleanup_r2(conn, reel_id, clients, log)
        live = [p for p in publications(conn, reel_id) if p["state"] not in ("unscheduled", "waiting")]
        if reel_row(conn, reel_id)["status"] == "deleted" and any(p["state"] == "published" for p in live):
            set_reel(conn, reel_id, clock(), status="published")
    send(
        about_reel(
            reel_id,
            "📤 Reposted:\n"
            + "\n".join(f"{'✅' if st == 'published' else '❌'} {label(ch)}: {v}" for ch, (st, v) in result.items()),
        ),
        None,
    )
    return result
