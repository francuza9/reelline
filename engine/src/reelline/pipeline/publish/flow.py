"""Publishing one reel: the shared steps, the R2 upload and each channel's post."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from reelline.config import conf
from reelline.core.errors import PublishError
from reelline.paths import stored_path
from reelline.pipeline import db
from reelline.pipeline.publish.base import CHANNEL_ERRORS, Outcome, enabled_channels, label, utcnow
from reelline.pipeline.publish.clients import Clients
from reelline.pipeline.publish.guard import channel_files, guard, reel_row, shown_images, texts
from reelline.pipeline.publish.poll import _comment_state
from reelline.pipeline.publish.queue import get_pub, publications, set_pub, set_reel
from reelline.publish.base import Post
from reelline.storage.blob import StorageError, key_in, new_key


def fail_shared(conn: sqlite3.Connection, out: Outcome, pubs: list[sqlite3.Row], error: str) -> Outcome:
    out.shared = error
    for p in pubs:
        set_pub(conn, p["id"], state="failed", error=error[:800])
        out.results[p["channel"]] = ("failed", error)
    return out


@dataclass(frozen=True)
class Urls:
    public: str  # the r2.dev URL Buffer fetches (plain: HEAD and GET work)
    presigned: str  # Facebook's (fetched at upload)


def upload_and_presign(
    conn: sqlite3.Connection,
    reel: sqlite3.Row,
    clients: Clients,
    now: datetime,
    slot: datetime | None,
    channels: list[str] | None = None,
) -> dict[str, Urls]:
    """Each channel's file (channel_files(); every enabled channel without `channels`) in
    R2 under the reel's unguessable prefix (its existing one on a retry), by its name (a
    file two channels share goes up once; an upload is skipped when it's there already),
    and per channel its public URL and a presigned GET URL living until the slot (or now)
    + publish.url_after_slot_h, at most url_max_days. The keys go to reels.r2_keys
    before any upload, so a crash can't orphan one."""
    files = channel_files(reel, channels or enabled_channels())
    # An adapter that reads the file from disk (media "file") needs no copy in blob storage.
    files = {ch: f for ch, f in files.items() if _media(clients, ch) != "file"}
    keys = json.loads(reel["r2_keys"] or "[]")
    first = keys[0] if keys else new_key(reel["id"])
    objs = {ch: key_in(first, f.name) for ch, f in files.items()}
    new = [k for k in dict.fromkeys(objs.values()) if k not in keys]
    if new:
        set_reel(conn, reel["id"], r2_keys=json.dumps(keys + new))
    until = max(slot or now, now) + timedelta(hours=conf().publish.url_after_slot_h)
    lifetime = int(min(until - now, timedelta(days=conf().publish.url_max_days)).total_seconds())
    urls: dict[str, Urls] = {}
    by_obj: dict[str, Urls] = {}
    for ch, obj in objs.items():
        if obj not in by_obj:
            clients.blob.put(obj, files[ch], "video/mp4")
            by_obj[obj] = Urls(clients.blob.url(obj), clients.blob.url(obj, lifetime))
        urls[ch] = by_obj[obj]
    return urls


def _media(clients: Clients, channel: str) -> str:
    return clients.publishers[channel].media


def publish_reel(
    conn: sqlite3.Connection,
    reel_id: int,
    clients: Clients,
    *,
    channels: list[str] | None = None,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
    probe: Callable[[Path], dict] | None = None,
    statuses: tuple[str, ...] = ("approved", "partially_published"),
) -> Outcome:
    """Create the reel's waiting posts (`channels` only, when given)."""
    now = now or utcnow()
    reel = reel_row(conn, reel_id)
    out = Outcome(reel_id)
    if reel["status"] not in statuses:
        log(f"Reel #{reel_id} is {reel['status']}; nothing to publish")
        return out
    pubs = [
        p
        for p in publications(conn, reel_id)
        if p["state"] in ("waiting", "scheduling")
        and (channels is None or p["channel"] in channels)
        and conf().publish.channels.get(p["channel"])
    ]
    if not pubs:
        log(f"Reel #{reel_id}: no waiting publication")
        return out
    problems = guard(conn, reel, [p["channel"] for p in pubs], probe)
    if problems:
        return fail_shared(conn, out, pubs, "guard: " + "; ".join(problems))
    slot = datetime.fromisoformat(reel["publish_at"])
    go_now = reel["publish_mode"] == "now" or slot - now <= timedelta(minutes=conf().publish.now_within_min)
    out.when = None if go_now else slot
    t = texts(conn, reel)
    # The reel's credits page (it shows images) and its shortlinks must be live before
    # anything posts (the guard has checked the caption links them).
    from reelline.pipeline.credits import CreditsError
    from reelline.pipeline.shortlinks import ShortlinkError, reel_links

    try:
        if shown_images(reel) or reel_links(conn, reel_id):
            if clients.site is None:
                raise CreditsError("no site deployer configured")
            site = clients.site(conn, reel_id)
            out.credits_url, out.links = site.get("credits"), site.get("links", [])
    except (CreditsError, ShortlinkError) as exc:
        log(f"Reel #{reel_id}: site deploy or check failed, nothing posted: {exc}")
        return fail_shared(conn, out, pubs, f"credits page: {exc}")
    try:
        urls = upload_and_presign(conn, reel, clients, now, None if go_now else slot, [p["channel"] for p in pubs])
    except StorageError as exc:
        log(f"Reel #{reel_id}: R2 upload failed, nothing posted: {exc}")
        return fail_shared(conn, out, pubs, f"R2 upload: {exc}")
    for p in pubs:
        ch = p["channel"]
        set_pub(
            conn,
            p["id"],
            state="scheduling",
            attempts=p["attempts"] + 1,
            mode="now" if go_now else "scheduled",
            due_at=db.now_iso(now if go_now else slot),
            error=None,
        )
        try:
            out.warnings += place(conn, get_pub(conn, p["id"]), clients, reel, t, urls.get(ch), out.when, now)
            out.results[ch] = ("scheduled", None)
            log(f"Reel #{reel_id}: {label(ch)} {'posted now' if go_now else 'scheduled'}")
        except CHANNEL_ERRORS + (PublishError,) as exc:
            set_pub(conn, p["id"], state="failed", error=str(exc)[:800])
            out.results[ch] = ("failed", str(exc))
            log(f"Reel #{reel_id}: {label(ch)} failed: {exc}")
    return out


def place(
    conn: sqlite3.Connection,
    pub: sqlite3.Row,
    clients: Clients,
    reel: sqlite3.Row,
    t: dict,
    urls: Urls | None,
    when: datetime | None,
    now: datetime,
) -> list[str]:
    """One channel's post, through its adapter. The adapter's id is stored as soon as it has one (before
    anything else can fail); a retry hands it the stored one. Returns the adapter's warnings."""
    ch = pub["channel"]
    cover = stored_path(reel["cover_path"]) if reel["cover_path"] else None
    post = Post(
        ch,
        t["caption"],
        channel_files(reel, [ch])[ch],
        public_url=urls.public if urls else None,
        signed_url=urls.presigned if urls else None,
        title=t["youtube_title"],
        first_comment=t["source_line"] if ch == "instagram" and conf().publish.instagram_first_comment else None,
        cover=cover,
        cover_offset_ms=t.get("cover_offset_ms"),
        when=when,
    )
    placed = clients.publishers[ch].publish(
        post, existing=pub["external_id"], now=now, remember=lambda id: set_pub(conn, pub["id"], external_id=id)
    )
    set_pub(conn, pub["id"], state="scheduled", external_id=placed.id, comment_state=_comment_state(ch))
    return placed.warnings
