"""The live test: a real scheduled post per channel, read back, then deleted."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import PublishError
from reelline.paths import stored_path
from reelline.pipeline import db
from reelline.pipeline.publish.base import BUFFER_CHANNELS, CHANNEL_ERRORS, enabled_channels, label, utcnow
from reelline.pipeline.publish.clients import Clients, platform_client
from reelline.pipeline.publish.flow import Urls
from reelline.pipeline.publish.guard import channel_files, guard, reel_row, texts
from reelline.publish.base import QueueFull
from reelline.publish.buffer import BufferError, post_input
from reelline.publish.meta import FacebookError, error_text, publish_status
from reelline.storage.blob import StorageError, key_in, new_key, strip_query


def redact(value):
    """Any signed URL in `value` (nested) cut to its path: what the APIs read back may
    contain the presigned URL, and the report and logs never hold a signature."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and ("X-Amz-" in value or "Signature=" in value):
        return " ".join(
            strip_query(w) + "?<signature>" if ("X-Amz-" in w or "Signature=" in w) else w for w in value.split(" ")
        )
    return value


def curl_check(url: str) -> dict:
    """The presigned URL fetched from outside our code: curl HEAD, then a 1 KB GET
    (a presigned GET's signature covers the method, so S3-style HEAD may be refused)."""

    def run(args):
        r = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code} %{content_type} %{size_download}", *args, url],
            capture_output=True,
            text=True,
            timeout=60,
        )
        code, ctype, size = (r.stdout.split(" ") + ["", "", ""])[:3]
        return {
            "http": int(code or 0),
            "content_type": ctype,
            "bytes": int(float(size or 0)),
            "error": r.stderr.strip()[:200] or None,
        }

    return {"head": run(["-I"]), "get_range": run(["-r", "0-1023"])}


def live_test(
    conn: sqlite3.Connection,
    reel_id: int,
    clients: Clients,
    *,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
    probe=None,
    sleep: Callable[[float], None] = time.sleep,
    curl: Callable[[str], dict] = curl_check,
    out_dir: Path | None = None,
    channels: list[str] | None = None,
) -> dict:
    """Everything but going live (module docstring). Returns the report (also written
    to out_dir); report["ok"] says whether every step passed."""
    now = now or utcnow()
    reel = reel_row(conn, reel_id)
    channels = [c for c in enabled_channels() if channels is None or c in channels]
    out_dir = out_dir or conf().storage.publish_test_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"reel_{reel_id}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    report: dict = {"reel_id": reel_id, "started_at": db.now_iso(now), "channels": {}, "steps": [], "ok": False}
    created: dict[str, str] = {}

    def save():
        path.write_text(json.dumps(redact(report), ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    def step(name: str, ok: bool, detail=None):
        report["steps"].append({"step": name, "ok": ok, "detail": detail})
        log(f"{'✅' if ok else '❌'} {name}" + (f": {detail}" if detail is not None and not ok else ""))
        save()
        return ok

    problems = guard(conn, reel, channels, probe)
    if not step("guard", not problems, problems or None):
        return report
    t = texts(conn, reel)
    due_at = (now + timedelta(hours=conf().publish.live_test_ahead_h)).replace(second=0, microsecond=0)
    due_at += timedelta(minutes=(5 - due_at.minute % 5) % 5)
    report["due_at"] = db.now_iso(due_at)
    # Each channel's own file (its share arrow), under one test prefix; a shared file once.
    files = channel_files(reel, channels)
    first = new_key(reel_id)
    objs = {ch: key_in(first, f.name) for ch, f in files.items()}
    keys = list(dict.fromkeys(objs.values())) or [first]
    links: dict[str, Urls] = {}
    try:
        for ch, obj in objs.items():
            if obj not in links:
                clients.blob.put(obj, files[ch], "video/mp4")
                links[obj] = Urls(
                    clients.blob.url(obj),
                    clients.blob.url(
                        obj, int((due_at - now + timedelta(hours=conf().publish.url_after_slot_h)).total_seconds())
                    ),
                )
    except StorageError as exc:
        step("r2 upload + presign", False, str(exc))
        _test_cleanup(clients, created, keys, step)
        return report
    step(
        "r2 upload + presign",
        True,
        {obj: {"url": strip_query(u.presigned), "public": u.public} for obj, u in links.items()},
    )
    if not all(
        [
            url_checks(
                curl,
                u.presigned,
                u.public,
                report.setdefault("url_checks", {}).setdefault(obj, {}),
                step,
                obj.rsplit("/", 1)[1] if len(links) > 1 else "",
            )
            for obj, u in links.items()
        ]
    ):
        _test_cleanup(clients, created, keys, step)
        return report

    try:
        # Schedule.
        limit = None
        due_iso = due_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for ch in channels:
            entry = report["channels"].setdefault(ch, {})
            try:
                if ch == "facebook":
                    vid = _test_facebook(
                        clients, reel, t, links[objs[ch]].presigned, due_at, now, sleep, created, entry
                    )
                    entry["external_id"] = vid
                else:
                    buffer = platform_client(clients, ch)
                    limit = limit if limit is not None else buffer.scheduled_limit()
                    queued = buffer.queued(ch)
                    if queued >= limit:
                        raise QueueFull(f"queue full: {queued} of {limit}")
                    payload = post_input(
                        buffer.channels[ch],
                        ch,
                        t["caption"],
                        links[objs[ch]].public,
                        due_at=due_iso,
                        youtube_title=t["youtube_title"],
                        first_comment=t["source_line"]
                        if ch == "instagram" and conf().publish.instagram_first_comment
                        else None,
                        thumbnail_offset_ms=t.get("cover_offset_ms"),
                    )
                    entry["sent"] = json.loads(json.dumps(payload, default=str))
                    post = buffer.create_post(ch, payload)
                    created[ch] = post["id"]
                    save()
                    entry["external_id"] = post["id"]
                    back = buffer.get_post(post["id"], ch)
                    entry["read_back"] = back
                    if back is None or back["status"] != "scheduled":
                        raise BufferError(f"read back: {back and back['status']}")
                step(f"{label(ch)} scheduled for {report['due_at']} and read back", True)
            except CHANNEL_ERRORS + (PublishError,) as exc:
                entry["error"] = str(exc)
                step(f"{label(ch)} scheduled", False, str(exc))
    finally:
        _test_cleanup(clients, created, keys, step, report)
    report["ok"] = all(s["ok"] for s in report["steps"])
    report["finished_at"] = db.now_iso(utcnow())
    save()
    log(f"Report: {path}")
    return redact(report)


def url_checks(curl: Callable[[str], dict], presigned: str, public: str, report: dict, step, what: str = "") -> bool:
    """Both URLs of one object fetched from outside our code (curl): the public one by
    HEAD and GET (Buffer checks it), the presigned one by GET (Facebook). `what` names the
    file in the steps (a reel with one file per platform)."""

    def ok(r):
        return r["http"] in (200, 206) and r["content_type"].startswith("video/")

    of = f" of {what}" if what else ""
    report["url_check"] = {"presigned": curl(presigned), "public": curl(public)}
    a = step(
        f"presigned URL{of} GET (curl; Facebook's)",
        ok(report["url_check"]["presigned"]["get_range"]),
        report["url_check"]["presigned"],
    )
    b = step(
        f"public URL{of} HEAD + GET (curl; Buffer's)",
        ok(report["url_check"]["public"]["head"]) and ok(report["url_check"]["public"]["get_range"]),
        report["url_check"]["public"],
    )
    return a and b


def _test_facebook(
    clients: Clients,
    reel: sqlite3.Row,
    t: dict,
    url: str,
    due_at: datetime,
    now: datetime,
    sleep,
    created: dict,
    entry: dict,
) -> str:
    fb = platform_client(clients, "facebook")
    entry["page"] = {k: str(v) for k, v in fb.check_page().items()}
    vid, upload_url = fb.start()
    created["facebook"] = vid
    fb.upload(vid, upload_url, url)
    waited = 0.0
    while True:
        status = fb.status(vid)
        if publish_status(status) == "error":
            raise FacebookError(f"upload: {error_text(status)}")
        if (status.get("uploading_phase") or {}).get("status") == "complete" or status.get("video_status") in (
            "upload_complete",
            "processing",
            "ready",
        ):
            break
        if waited >= conf().publish.facebook_upload_wait_s:
            raise FacebookError(f"upload not complete after {waited:.0f}s")
        sleep(conf().publish.facebook_upload_poll_s)
        waited += conf().publish.facebook_upload_poll_s
    try:
        fb.thumbnail(vid, stored_path(reel["cover_path"]))
        entry["cover"] = "cover.png set as the preferred thumbnail"
    except FacebookError as exc:
        entry["cover"] = f"FAILED: {exc}"
    finish = fb.finish(vid, t["caption"], due_at)
    entry["sent"] = {
        "description": t["caption"],
        "video_state": "SCHEDULED",
        "scheduled_publish_time": int(due_at.timestamp()),
        "is_ai_generated": conf().publish.ai_generated,
        "file_url": strip_query(url) + "?<signature>",
        "finish_response": finish,
    }
    # Meta processes the video (and a copyright check) before it's scheduled.
    waited = 0.0
    while True:
        status = fb.status(vid)
        if (
            publish_status(status) in ("scheduled", "published", "error")
            or waited >= conf().publish.facebook_upload_wait_s
        ):
            break
        sleep(conf().publish.facebook_upload_poll_s)
        waited += conf().publish.facebook_upload_poll_s
    entry["processing_wait_s"] = waited
    entry["read_back"] = {"status": status}
    try:
        entry["read_back"].update(
            fb._call(
                "fb_read",
                "GET",
                fb.graph(vid),
                params={"fields": "id,description,published,scheduled_publish_time,thumbnails{uri,is_preferred}"},
                external_id=vid,
            )
        )
    except FacebookError as exc:
        entry["read_back"]["fields_error"] = str(exc)
    if publish_status(status) != "scheduled":
        raise FacebookError(f"read back: publish_status {publish_status(status)} ({status})")
    return vid


def _test_cleanup(clients: Clients, created: dict, objs: list[str], step, report: dict | None = None) -> None:
    """Delete everything the test made; verify it's gone."""
    for ch, ext in list(created.items()):
        try:
            clients.publishers[ch].delete(ch, ext)
        except CHANNEL_ERRORS as exc:
            step(f"{label(ch)} deleted", False, f"{exc} - DELETE IT BY HAND ({ext})")
            continue
        try:
            gone = not clients.publishers[ch].exists(ch, ext)
            detail = None if gone else f"still there ({ext})"
        except CHANNEL_ERRORS as exc:  # the read-back's own error: the listing below decides
            gone, detail = False, f"deleted, but reading it back failed: {exc}"
        step(f"{label(ch)} deleted (verified gone)", gone, detail)
        if gone:
            created.pop(ch)
    try:
        clients.blob.delete(objs)
        left = clients.blob.list(objs[0].rsplit("/", 1)[0] + "/")
        step("R2 objects deleted", not left, left or None)
    except StorageError as exc:
        step("R2 objects deleted", False, str(exc))
    if report is None:
        return
    # The queues and the Page, empty again?
    leftovers = {}
    for ch in BUFFER_CHANNELS:
        if conf().publish.channels.get(ch):
            posts = platform_client(clients, ch).find_posts(
                ch, ["scheduled", "draft", "needs_approval", "sending", "error"]
            )
            if posts:
                leftovers[ch] = [p["id"] for p in posts]
    if conf().publish.channels.get("facebook"):
        reels = platform_client(clients, "facebook").reels()
        if reels:
            leftovers["facebook"] = [r["id"] for r in reels]
    step("Buffer queues and Facebook reels empty again", not leftovers, leftovers or None)
