"""Link comments: once a post is live, a comment with the reel's links goes under it on
every platform whose API allows it, behind a flag per channel (publish.comments; a
channel stays off until it's authorized, channel docs/comments.md). The caption keeps its
links too.

The text (comment_text(), publish.comment_lines):
  <source label>: <the source link the caption has: the reel's shortlink>
  <credits label>: <the credits page link>          only when the reel shows images

Per channel, with the post's id from its publication:
  facebook   POST /{video}/comments as the Page (the Page token; Graph comments can't be
             pinned)
  youtube    commentThreads.insert on the video (Buffer's link has its id); needs the
             youtube.force-ssl scope on YOUTUBE_REFRESH_TOKEN; 50 quota units; the API
             can't pin a comment
  instagram  the media found in GET /{ig-user}/media by the post's shortcode (Buffer's
             link), else by its caption; then POST /{ig-media}/comments; needs
             instagram_manage_comments on META_PAGE_TOKEN
  tiktok     TikTok API for Business, Accounts API: POST business/comment/create/
             (TIKTOK_BUSINESS_*: an app approved for "Account Comment", the account's token)

States (publications.comment_state; migration 22 adds comment_error, comment_at):
  NULL       the post isn't live yet
  off        it went live while its channel's flag was off (never commented later)
  skipped    no text (no source line in the caption)
  pending    to post: tried again every publish.poll_interval_min (Instagram's media not
             listed yet) or at once after a Retry; still not found
             publish.comment_wait_min after going live: failed
  posting    the call is out; a crash here is settled by looking for our comment first
  posted     comment_id holds its id
  failed     comment_error says why; one alert with a Retry button for that channel. The
             publication itself stays published: a comment never fails a post.
  buffer     Buffer's own Instagram first comment (paid plans; publish.instagram_first_comment)

Idempotent: one comment per publication. Before posting again after a crash ("posting"),
the post's comments are searched for ours (Facebook, YouTube, Instagram); TikTok's can't
be searched here, so a crash there fails with "check by hand" (Retry posts again).

CLI: python -m reelline.pipeline.comments status <reel_id>  (each channel's comment state).
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from reelline.config import conf
from reelline.core.errors import ReellineError
from reelline.notify.text import about_reel
from reelline.pipeline import db
from reelline.storage.blob import no_record

CREDITS_PREFIX = conf().publish.credits_page.line.split("{")[0]


class CommentError(ReellineError, RuntimeError):
    """A comment that couldn't be posted (the platform's message, no secrets)."""


class NotYet(CommentError):
    """The post isn't where its comment can go yet (Instagram's media not listed): try later."""


def label(channel: str) -> str:
    return conf().publish.labels[channel]


def norm(text: str | None) -> str:
    return " ".join((text or "").split())


# ---- The text ------------------------------------------------------------------------------


def comment_text(caption: str | None) -> str | None:
    """The comment for a reel with this caption: its source link (from the caption's
    source line: the reel's shortlink, or the source URL of a reel from before them) and
    its credits line when it has one. None without a source line."""

    prefix = conf().steps.caption.source_line.split("{", 1)[0]
    lines = (caption or "").splitlines()
    source = next((x for x in lines if x.startswith(prefix)), None)
    if source is None or " - " not in source:
        return None
    out = [conf().publish.comment_lines["source"].format(url=source.rsplit(" - ", 1)[1].strip())]
    credits = next((x for x in lines if x.startswith(CREDITS_PREFIX)), None)
    if credits:
        out.append(conf().publish.comment_lines["credits"].format(url=credits[len(CREDITS_PREFIX) :].strip()))
    return "\n".join(out)


# ---- The platforms -------------------------------------------------------------------------


def _id_from(pattern: str, url: str | None, what: str) -> str:
    m = re.search(pattern, url or "")
    if not m:
        raise CommentError(f"no {what} in the post's link ({url or 'none'})")
    return m.group(1)


class FacebookComments:
    def __init__(self, fb):
        self.fb = fb

    def post(self, pub: sqlite3.Row, text: str) -> str:
        return self.fb.comment(pub["external_id"], text)

    def find(self, pub: sqlite3.Row, text: str) -> str | None:
        data = self.fb._call(
            "fb_comments",
            "GET",
            self.fb.graph(f"{pub['external_id']}/comments"),
            params={"fields": "id,message,from"},
            external_id=pub["external_id"],
        ).get("data", [])
        return next(
            (
                c["id"]
                for c in data
                if norm(c.get("message")) == norm(text)
                and (c.get("from") or {}).get("id") == conf().publish.facebook_page_id
            ),
            None,
        )


class InstagramComments:
    """Through the Graph API with the Page token (FacebookClient._call)."""

    def __init__(self, fb):
        self.fb, self._ig = fb, None

    def ig(self) -> dict:
        if self._ig is None:
            page = self.fb._call(
                "ig_account",
                "GET",
                self.fb.graph(conf().publish.facebook_page_id),
                params={"fields": "instagram_business_account{id,username}"},
            )
            self._ig = page.get("instagram_business_account")
            if not self._ig:
                raise CommentError("no Instagram account linked to the Page (or no instagram_basic on the token)")
        return self._ig

    def media_id(self, pub: sqlite3.Row, caption: str | None) -> str:
        """The post's IG media id: by the shortcode in Buffer's link, else the caption."""
        from reelline.pipeline.stats import ig_shortcode

        code = ig_shortcode(pub["url"])
        items = self.fb._call(
            "ig_media",
            "GET",
            self.fb.graph(f"{self.ig()['id']}/media"),
            params={"fields": "id,shortcode,permalink,timestamp,caption", "limit": 25},
        ).get("data", [])
        found = next(
            (m for m in items if code and (m.get("shortcode") == code or code in (m.get("permalink") or ""))), None
        )
        if found is None and caption:
            found = next((m for m in items if norm(m.get("caption")) == norm(caption)), None)
        if found is None:
            raise NotYet(f"the post ({pub['url'] or 'no link'}) isn't in the account's latest media yet")
        return found["id"]

    def post(self, pub: sqlite3.Row, text: str, caption: str | None = None) -> str:
        media = self.media_id(pub, caption)
        body = self.fb._call(
            "ig_comment",
            "POST",
            self.fb.graph(f"{media}/comments"),
            data={"message": text},
            external_id=media,
            summary={"message": text},
        )
        return str(body.get("id"))

    def find(self, pub: sqlite3.Row, text: str, caption: str | None = None) -> str | None:
        media = self.media_id(pub, caption)
        data = self.fb._call(
            "ig_comments",
            "GET",
            self.fb.graph(f"{media}/comments"),
            params={"fields": "id,text,username"},
            external_id=media,
        ).get("data", [])
        return next(
            (
                c["id"]
                for c in data
                if norm(c.get("text")) == norm(text) and c.get("username") == self.ig().get("username")
            ),
            None,
        )


class YouTubeComments:
    """commentThreads with the channel owner's OAuth token (reelline/pipeline/youtube.py)."""

    def __init__(self, yt):

        self.yt, self.base, self._channel = yt, conf().steps.analytics.youtube_data_url, None

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.yt.access_token()}"}

    def channel_id(self) -> str:
        if self._channel is None:
            items = (
                self.yt._call(
                    "yt_channel_mine",
                    "GET",
                    f"{self.base}/channels",
                    params={"part": "id", "mine": "true"},
                    headers=self._auth(),
                ).get("items")
                or []
            )
            if not items:
                raise CommentError("the YouTube token has no channel")
            self._channel = items[0]["id"]
        return self._channel

    def video_id(self, pub: sqlite3.Row) -> str:
        return _id_from(r"(?:[?&]v=|/shorts/|youtu\.be/)([\w-]{11})", pub["url"], "YouTube video id")

    def post(self, pub: sqlite3.Row, text: str) -> str:
        body = {
            "snippet": {
                "channelId": self.channel_id(),
                "videoId": self.video_id(pub),
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        }
        return str(
            self.yt._call(
                "yt_comment",
                "POST",
                f"{self.base}/commentThreads",
                params={"part": "snippet"},
                json=body,
                headers=self._auth(),
            ).get("id")
        )

    def find(self, pub: sqlite3.Row, text: str) -> str | None:
        items = (
            self.yt._call(
                "yt_comments",
                "GET",
                f"{self.base}/commentThreads",
                params={"part": "snippet", "videoId": self.video_id(pub), "maxResults": 100, "textFormat": "plainText"},
                headers=self._auth(),
            ).get("items")
            or []
        )
        mine = self.channel_id()
        for item in items:
            top = item["snippet"]["topLevelComment"]["snippet"]
            if (
                norm(top.get("textOriginal") or top.get("textDisplay")) == norm(text)
                and (top.get("authorChannelId") or {}).get("value") == mine
            ):
                return item["id"]
        return None


class TikTokComments:
    """TikTok API for Business, Accounts API v1.3 (the TikTok for Business portal docs,
    "Create a new comment on an owned video", checked 2026-10-16): POST
    /business/comment/create/ with the Access-Token header and {business_id, video_id,
    text}. The access token is refreshed from TIKTOK_BUSINESS_REFRESH_TOKEN with the app's
    id and secret. Built from the docs: untested until the app is approved."""

    @property
    def URL(self) -> str:
        return conf().publish.tiktok_business.api_url

    def __init__(self, settings=None, http: httpx.Client | None = None, record: Callable[..., None] = no_record):
        if settings is None:
            from reelline.config.credentials import load_tiktok_business

            settings = load_tiktok_business()
        self.s, self.record = settings, record
        self.http = http or httpx.Client(timeout=conf().publish.http_timeout_s)
        self._token: str | None = None

    def _call(self, action: str, path: str, body: dict, token: str | None = None) -> dict:
        secrets = [x for x in (self.s.app_secret, self.s.refresh_token, token) if x]
        headers = {"Content-Type": "application/json", **({"Access-Token": token} if token else {})}
        try:
            r = self.http.post(f"{self.URL}{path}", json=body, headers=headers)
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CommentError(f"TikTok {action}: {type(exc).__name__}") from None
        message = None if data.get("code") == 0 else f"{data.get('message')} (code {data.get('code')})"
        for secret in secrets:
            message = message.replace(secret, "<secret>") if message else message
        self.record(
            action=action,
            method="POST",
            endpoint=f"{self.URL}{path}",
            channel="tiktok",
            status=r.status_code,
            error=message,
        )
        if message:
            raise CommentError(f"TikTok {action}: {message}")
        return data.get("data") or {}

    def token(self) -> str:
        if self._token is None:
            data = self._call(
                "tt_token",
                "/tt_user/oauth2/refresh_token/",
                {
                    "client_id": self.s.app_id,
                    "client_secret": self.s.app_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self.s.refresh_token,
                },
            )
            self._token = data["access_token"]
        return self._token

    def post(self, pub: sqlite3.Row, text: str) -> str:
        video = _id_from(r"/video/(\d+)", pub["url"], "TikTok video id")
        data = self._call(
            "tt_comment",
            "/business/comment/create/",
            {"business_id": self.s.business_id, "video_id": video, "text": text},
            token=self.token(),
        )
        return str(data.get("comment_id") or data.get("id") or "")

    def find(self, pub: sqlite3.Row, text: str) -> str | None:
        raise CommentError("a TikTok comment can't be looked up here: check the post by hand, then Retry")


class CommentClients:
    """Each channel's commenter, made when first needed (a channel that's off never loads
    its credentials)."""

    def __init__(self, facebook, record: Callable[..., None] = no_record, makers: dict | None = None):
        def youtube():
            from reelline.publish.youtube import YouTubeClient

            return YouTubeComments(YouTubeClient(record=record))

        self._makers = {
            "facebook": lambda: FacebookComments(facebook),
            "instagram": lambda: InstagramComments(facebook),
            "youtube": youtube,
            "tiktok": lambda: TikTokComments(record=record),
            **(makers or {}),
        }
        self._made: dict = {}

    def __call__(self, channel: str):
        if channel not in self._made:
            self._made[channel] = self._makers[channel]()
        return self._made[channel]


# ---- The flow ------------------------------------------------------------------------------


def set_comment(conn: sqlite3.Connection, pub_id: int, now: datetime | None = None, **fields) -> None:
    fields["updated_at"] = db.now_iso(now)
    with db.transaction(conn):
        conn.execute(
            f"UPDATE publications SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), pub_id)
        )


def retry_markup(pub: sqlite3.Row) -> list:
    from reelline.notify.base import buttons

    return buttons(
        [[(conf().publish.buttons["retry_comment"].format(channel=label(pub["channel"])), f"pc:{pub['id']}:rt")]]
    )


def went_live(conn: sqlite3.Connection, pub: sqlite3.Row, clients, send, log, now: datetime) -> str:
    """A post just went live: its comment pending (and tried now) when its channel's flag is
    on, else off. Buffer's own Instagram first comment stays as it is."""
    if pub["comment_state"] in ("posted", "buffer"):
        return pub["comment_state"]
    if not conf().publish.comments.get(pub["channel"]):
        set_comment(conn, pub["id"], now, comment_state="off")
        return "off"
    set_comment(conn, pub["id"], now, comment_state="pending", comment_error=None)
    return post(
        conn, conn.execute("SELECT * FROM publications WHERE id = ?", (pub["id"],)).fetchone(), clients, send, log, now
    )


def post(conn: sqlite3.Connection, pub: sqlite3.Row, clients, send, log, now: datetime) -> str:
    """Post (or find, after a crash) the publication's comment; its comment_state after."""
    reel = conn.execute("SELECT * FROM reels WHERE id = ?", (pub["reel_id"],)).fetchone()
    text = comment_text(reel["caption"])
    if text is None:
        set_comment(conn, pub["id"], now, comment_state="skipped", comment_error="no source line in the caption")
        return "skipped"
    ch = pub["channel"]
    extra = {"caption": reel["caption"]} if ch == "instagram" else {}
    try:
        commenter = clients.comments(ch)
        found = commenter.find(pub, text, **extra) if pub["comment_state"] == "posting" else None
        if found is None:
            set_comment(conn, pub["id"], now, comment_state="posting")
            found = commenter.post(pub, text, **extra)
    except NotYet as exc:
        went = datetime.fromisoformat(pub["published_at"]) if pub["published_at"] else now
        if now - went < timedelta(minutes=conf().publish.comment_wait_min):
            set_comment(conn, pub["id"], now, comment_state="pending", comment_error=str(exc)[:500])
            log(f"Reel #{pub['reel_id']}: {label(ch)} comment waits: {exc}")
            return "pending"
        return _failed(conn, pub, str(exc), send, log, now)
    except Exception as exc:  # the platform's error, a missing credential, a network failure
        ours = type(exc).__module__.split(".")[0] in ("reelline",)  # already says what failed
        return _failed(conn, pub, str(exc) if ours else f"{type(exc).__name__}: {exc}", send, log, now)
    set_comment(
        conn, pub["id"], now, comment_state="posted", comment_id=found, comment_error=None, comment_at=db.now_iso(now)
    )
    log(f"Reel #{pub['reel_id']}: {label(ch)} links comment posted ({found})")
    return "posted"


def _failed(conn, pub, error: str, send, log, now) -> str:
    set_comment(conn, pub["id"], now, comment_state="failed", comment_error=error[:800])
    send(
        about_reel(
            pub["reel_id"],
            f"⚠️ The links comment on {label(pub['channel'])} failed: {error[:400]}. The post itself is live.",
        ),
        retry_markup(pub),
    )
    log(f"Reel #{pub['reel_id']}: {label(pub['channel'])} comment failed: {error}")
    return "failed"


def run_pending(conn: sqlite3.Connection, clients_for: Callable[[int], object], send, log, now: datetime) -> list[str]:
    """Every live post whose comment is pending (or was posting when a worker died). One
    that is waiting (it has an error from its last try) is tried again once every
    publish.poll_interval_min, not on every round; a Retry (no error) goes at once."""
    wait = timedelta(minutes=conf().publish.poll_interval_min)
    done = []
    for pub in conn.execute("""SELECT * FROM publications WHERE state = 'published'
                               AND comment_state IN ('pending', 'posting') ORDER BY id""").fetchall():
        if (
            pub["comment_state"] == "pending"
            and pub["comment_error"]
            and pub["updated_at"]
            and datetime.fromisoformat(pub["updated_at"]) > now - wait
        ):
            continue
        done.append(post(conn, pub, clients_for(pub["reel_id"]), send, log, now))
    return done


def retry(conn: sqlite3.Connection, pub_id: int) -> str:
    """A failed comment back to pending (the next poll round posts it)."""
    pub = conn.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()
    if pub is None:
        return "expired"
    if pub["comment_state"] != "failed":
        return f"already {pub['comment_state'] or 'waiting'}"
    set_comment(conn, pub_id, comment_state="pending", comment_error=None)
    return "retrying"


def status_text(conn: sqlite3.Connection, reel_id: int) -> str:
    out = [f"Reel #{reel_id}: links comments"]
    for p in conn.execute("SELECT * FROM publications WHERE reel_id = ? ORDER BY id", (reel_id,)).fetchall():
        flag = "on" if conf().publish.comments.get(p["channel"]) else "off"
        out.append(
            f"  {label(p['channel']):<9} flag {flag:<3} {p['comment_state'] or '-':<8} {p['comment_id'] or ''} "
            f"{p['comment_error'] or ''}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reelline.pipeline.comments", description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("status", help="each channel's comment state for a reel")
    p.add_argument("reel_id", type=int)
    args = parser.parse_args(argv)
    print(status_text(db.connect(args.db), args.reel_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
