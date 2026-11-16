"""Reading a published post back from its platform and comparing it with the plan."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable

from reelline.config import conf
from reelline.pipeline.publish.base import BUFFER_CHANNELS, label
from reelline.pipeline.publish.clients import Clients, platform_client
from reelline.pipeline.publish.guard import reel_row, texts
from reelline.pipeline.publish.queue import publications
from reelline.publish.buffer import post_input


def norm(text: str | None) -> str:
    return " ".join((text or "").split())


def expected_posts(conn: sqlite3.Connection, reel: sqlite3.Row, clients: Clients) -> dict:
    """What we send each channel (the payload fields worth checking on the other side)."""
    t = texts(conn, reel)
    out = {}
    for ch in BUFFER_CHANNELS:
        inp = post_input(
            platform_client(clients, ch).channels[ch],
            ch,
            t["caption"],
            "-",
            due_at=None,
            youtube_title=t["youtube_title"],
            first_comment=t["source_line"] if ch == "instagram" and conf().publish.instagram_first_comment else None,
            thumbnail_offset_ms=t.get("cover_offset_ms"),
        )
        out[ch] = {
            "text": inp["text"],
            "metadata": inp["metadata"][ch],
            "thumbnailOffset": (inp["assets"][0]["video"].get("metadata") or {}).get("thumbnailOffset"),
        }
    from reelline.pipeline.comments import comment_text

    out["facebook"] = {
        "description": t["caption"],
        "is_ai_generated": conf().publish.ai_generated,
        "cover": "cover.png",
        "comment": comment_text(t["caption"]) if conf().publish.comments["facebook"] else None,
    }
    return out


def page_facts(url: str, fetch: Callable[[str], str]) -> dict:
    """What a public post page shows (best effort, no login): title, description, YouTube
    category; TikTok's oEmbed caption."""
    import html

    facts: dict = {"url": url}
    try:
        if "tiktok.com" in url:
            # oEmbed wants the www. form; Buffer's externalLink lacks it.
            link = re.sub(r"^https://(?:m\.)?tiktok\.com/", "https://www.tiktok.com/", url)
            data = json.loads(fetch("https://www.tiktok.com/oembed?url=" + link))
            facts.update(
                caption=data.get("title"),
                thumbnail=bool(data.get("thumbnail_url")),
                author=data.get("author_unique_id") or data.get("author_name"),
            )
            return facts
        if "youtube.com" in url or "youtu.be" in url:
            data = json.loads(fetch("https://www.youtube.com/oembed?format=json&url=" + url))
            facts.update(title=data.get("title"), author=data.get("author_name"))
        page = fetch(url)
    except Exception as exc:  # network, login wall, JSON
        facts["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return facts

    def meta(name):
        m = re.search(rf'<meta[^>]+(?:property|name|itemprop)="{name}"[^>]+content="([^"]*)"', page)
        return html.unescape(m.group(1)) if m else None

    if "Sign in to confirm you" in page or "LOGIN_REQUIRED" in page:
        facts["blocked"] = "the platform shows this machine a sign-in / bot check"
        return facts
    if not facts.get("title"):
        facts["title"] = meta("og:title") or meta("title") or None
    facts["description"] = meta("og:description")
    if "youtube.com" in url or "youtu.be" in url:
        m = re.search(r'"category":"([^"]+)"', page)
        facts["category"] = m.group(1) if m else meta("genre")
        m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', page)
        if m:
            facts["description"] = json.loads(f'"{m.group(1)}"')
        m = re.search(r'"isFamilySafe":(true|false)', page)
        facts["family_safe"] = m.group(1) if m else None
    return facts


def http_text(url: str) -> str:
    import httpx

    r = httpx.get(
        url,
        follow_redirects=True,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    r.raise_for_status()
    return r.text


def verify(
    conn: sqlite3.Connection,
    reel_id: int,
    clients: Clients,
    fetch: Callable[[str], str] = http_text,
    channels: list[str] | None = None,
) -> dict:
    """Each live post read back (Buffer / Graph API) and from its public page, against
    what we sent; "diffs" lists every difference."""
    reel = reel_row(conn, reel_id)
    want = expected_posts(conn, reel, clients)
    out: dict = {}
    for p in publications(conn, reel_id):
        if channels is not None and p["channel"] not in channels:
            continue
        ch, e, diffs, unverified = p["channel"], want[p["channel"]], [], []
        entry: dict = {
            "state": p["state"],
            "url": p["url"],
            "external_id": p["external_id"],
            "diffs": diffs,
            "unverified": unverified,
        }
        out[ch] = entry
        if p["state"] == "deleted":
            unverified.append(f"{label(ch)}: {p['error']}")
            continue
        if p["state"] != "published":
            diffs.append(f"not published: {p['error']}")
            continue
        if ch == "facebook":
            fb = platform_client(clients, "facebook")
            got = fb._call(
                "fb_verify",
                "GET",
                fb.graph(p["external_id"]),
                external_id=p["external_id"],
                params={"fields": "description,published,permalink_url,thumbnails{is_preferred,uri}"},
            )
            comments = fb._call(
                "fb_verify_comments",
                "GET",
                fb.graph(f"{p['external_id']}/comments"),
                external_id=p["external_id"],
                params={"fields": "message,from"},
            ).get("data", [])
            entry["api"] = {
                "description": got.get("description"),
                "published": got.get("published"),
                "preferred_thumbnail": any(
                    t.get("is_preferred") for t in (got.get("thumbnails") or {}).get("data", [])
                ),
                "comments": comments,
            }
            if norm(got.get("description")) != norm(e["description"]):
                diffs.append("description differs from the caption")
            if e["comment"] and not any(
                norm(c.get("message")) == norm(e["comment"])
                and (c.get("from") or {}).get("id") == conf().publish.facebook_page_id
                for c in comments
            ):
                diffs.append("links comment by the Page not found")
            if not entry["api"]["preferred_thumbnail"]:
                diffs.append("no preferred (custom) thumbnail")
            continue
        post = platform_client(clients, ch).get_post(p["external_id"], ch) or {}
        meta = {k: v for k, v in (post.get("metadata") or {}).items() if k not in ("__typename", "annotations")}
        video = (post.get("assets") or [{}])[0].get("video") or {}
        entry["api"] = {
            "status": post.get("status"),
            "text": post.get("text"),
            "metadata": meta,
            "thumbnailOffset": video.get("thumbnailOffset"),
            "externalLink": post.get("externalLink"),
        }
        if post.get("text") != e["text"]:
            diffs.append("Buffer's text differs from the caption")
        for k, v in e["metadata"].items():
            got = meta.get(k)
            if k == "categoryId":
                got = (meta.get("category") or {}).get("categoryId")
            if got != v:
                diffs.append(f"Buffer metadata {k}: sent {v!r}, has {got!r}")
        if e["thumbnailOffset"] is not None and video.get("thumbnailOffset") != e["thumbnailOffset"]:
            diffs.append(f"thumbnailOffset: sent {e['thumbnailOffset']}, has {video.get('thumbnailOffset')}")
        unverified.append(f"{label(ch)} AI label, cover frame: only visible in the app (check by hand)")
        if p["url"]:
            page = page_facts(p["url"], fetch)
            entry["page"] = page
            if page.get("error"):
                unverified.append(f"public page not readable here ({page['error']})")
            if ch == "youtube":
                if page.get("title") is not None and norm(page["title"]) != norm(e["metadata"]["title"]):
                    diffs.append(f"YouTube title on the page: {page.get('title')!r}")
                if page.get("category"):
                    if page["category"] != "Education":
                        diffs.append(f"YouTube category on the page: {page['category']}")
                else:
                    unverified.append(
                        f"YouTube category and description on the page: "
                        f"{page.get('blocked') or 'not in the page'} (Buffer has category 27)"
                    )
                if page.get("description") is not None and norm(page["description"]) != norm(e["text"]):
                    diffs.append("YouTube description differs from the caption")
            elif ch == "tiktok":
                if page.get("caption") is not None and norm(page["caption"]) != norm(e["text"]):
                    diffs.append("TikTok caption on the page differs from the caption")
            elif ch == "instagram" and not page.get("description"):
                unverified.append("Instagram caption on the page: behind Instagram's login wall here")
    return out
