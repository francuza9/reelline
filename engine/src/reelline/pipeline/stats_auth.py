"""One-time authorizations for the extra stats sources (reelline/pipeline/stats.py), run on the
server by hand. They only read: they never post anything. Tokens are asked for without
echo, written to .env (a timestamped backup of it first), and never printed.

Usage:
  python -m reelline.pipeline.stats_auth meta
      After making a User token in Graph API Explorer (the app, the 5
      publishing scopes + read_insights, instagram_basic, instagram_manage_insights;
      the Page and the Instagram account ticked in the dialog): paste it here.
      It's exchanged for a long-lived token, the Page's token is read with
      GET /{page_id}?fields=access_token (as for publishing: /me/accounts comes back
      empty for this Page; a Page token from a long-lived user token never expires),
      its scopes, data-access expiry and linked Instagram account are shown, and
      META_PAGE_TOKEN is replaced in .env. Nothing is written unless the Page node and
      the new token's /me are both PUBLISH's facebook_page_id / facebook_page_name and
      the publishing scopes are all still there.
  python -m reelline.pipeline.stats_auth youtube
      Needs YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET (an OAuth client of type Desktop
      app in Google Cloud the project, consent screen In production). Prints
      a consent URL for both scopes (Analytics read-only, and youtube.force-ssl for the
      links comment, reelline/pipeline/comments.py): open it anywhere, signed in as the channel
      owner; the browser ends on a page that doesn't load (http://127.0.0.1:...?code=...):
      paste that whole URL back. YOUTUBE_REFRESH_TOKEN goes to .env after one Analytics
      query and the channel lookup (channels.list mine=true) prove it works.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import os
import secrets
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from reelline.config import conf
from reelline.core.errors import ReellineError

PUBLISHING_SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts", "pages_manage_engagement")
REDIRECT = "http://127.0.0.1:8765"


class AuthError(ReellineError, RuntimeError):
    pass


def set_env(path: Path, values: dict[str, str]) -> Path:
    """Replace (or append) KEY=value lines in the env file; returns the backup's path."""
    text = path.read_text(encoding="utf-8")
    backup = path.with_name(f"{path.name}.bak-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
    backup.write_text(text, encoding="utf-8")
    os.chmod(backup, 0o600)
    lines, seen = text.splitlines(), set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={values[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return backup


def _get(http: httpx.Client, url: str, params: dict, secret_values: list[str]) -> dict:
    r = http.get(url, params=params)
    body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
    if r.status_code >= 400 or "error" in body:
        msg = str(body.get("error") or f"HTTP {r.status_code}")
        for s in secret_values:
            msg = msg.replace(s, "<secret>")
        raise AuthError(msg)
    return body


def meta(http: httpx.Client | None = None) -> int:
    from reelline.config.credentials import load_meta

    s = load_meta()
    http = http or httpx.Client(timeout=30)
    g = conf().publish.graph_url
    short = getpass.getpass("Graph API Explorer user token (not shown): ").strip()
    if not short:
        print("No token given; nothing changed.")
        return 1
    hide = [short, s.app_secret, s.page_token]
    long_user = _get(
        http,
        f"{g}/oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": s.app_id,
            "client_secret": s.app_secret,
            "fb_exchange_token": short,
        },
        hide,
    )["access_token"]
    hide.append(long_user)
    want = (conf().publish.facebook_page_id, conf().publish.facebook_page_name)
    if s.page_id != want[0]:
        raise AuthError(f"META_PAGE_ID in .env is {s.page_id}, expected {want[0]}: nothing written")
    page = _get(http, f"{g}/{want[0]}", {"fields": "id,name,access_token", "access_token": long_user}, hide)
    if (str(page.get("id")), page.get("name")) != want or not page.get("access_token"):
        raise AuthError(
            f"The token has no access to Page {want[0]} ({want[1]}): got "
            f"{page.get('id')} {page.get('name')!r}{'' if page.get('access_token') else ' without a Page token'}"
        )
    token = page["access_token"]
    hide.append(token)
    me = _get(http, f"{g}/me", {"fields": "id,name", "access_token": token}, hide)
    if (str(me.get("id")), me.get("name")) != want:
        raise AuthError(
            f"The new token is for {me.get('id')} {me.get('name')!r}, not Page {want[0]} ({want[1]}): nothing written"
        )
    d = _get(http, f"{g}/debug_token", {"input_token": token, "access_token": f"{s.app_id}|{s.app_secret}"}, hide)[
        "data"
    ]
    scopes = set(d.get("scopes") or [])
    missing_publishing = [x for x in PUBLISHING_SCOPES if x not in scopes]
    if missing_publishing:
        raise AuthError(
            f"The new token lacks publishing scopes {missing_publishing}: nothing written "
            "(publishing would break). Tick them in Graph API Explorer and run again."
        )
    ig = _get(
        http,
        f"{g}/{conf().publish.facebook_page_id}",
        {"fields": "instagram_business_account{id,username}", "access_token": token},
        hide,
    ).get("instagram_business_account")
    expires = d.get("data_access_expires_at")
    print(
        f"Page {page['name']} ({page['id']}): token type {d.get('type')}, expires "
        f"{'never' if not d.get('expires_at') else datetime.fromtimestamp(d['expires_at'], UTC)}, data access "
        f"until {datetime.fromtimestamp(expires, UTC).date() if expires else '?'}"
    )
    print("Scopes: " + ", ".join(sorted(scopes)))
    for want in (
        conf().steps.analytics.meta_insights_scope,
        *conf().steps.analytics.meta_ig_scopes,
        "instagram_manage_comments",
    ):
        print(f"  {want}: {'yes' if want in scopes else 'MISSING'}")
    print(f"Instagram linked to the Page: {ig['username'] + ' (' + ig['id'] + ')' if ig else 'NO'}")
    backup = set_env(s.env_file, {"META_PAGE_TOKEN": token})
    print(
        f"META_PAGE_TOKEN replaced in {s.env_file} (backup {backup.name}). Stats pick the new scopes up on their "
        "next round; publishing uses the new token at once."
    )
    return 0


def youtube(http: httpx.Client | None = None) -> int:
    from reelline.config.credentials import load_youtube

    s = load_youtube()
    if not (s.client_id and s.client_secret):
        print(
            "YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET must be in .env first (Desktop app OAuth client, "
            f"project {conf().steps.analytics.google_project})."
        )
        return 1
    http = http or httpx.Client(timeout=30)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = (
        conf().steps.analytics.google_auth_url
        + "?"
        + urlencode(
            {
                "client_id": s.client_id,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "scope": f"{conf().steps.analytics.youtube_scope} {conf().steps.analytics.youtube_comment_scope}",
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    print(
        "Open this URL signed in as the YouTube channel's owner, allow, then copy the address of the page that "
        "doesn't load:\n\n" + url + "\n"
    )
    back = getpass.getpass("Paste that address (not shown): ").strip()
    q = parse_qs(urlparse(back).query)
    if q.get("state", [None])[0] != state or "code" not in q:
        print("That's not the redirect of this consent (state or code missing); nothing changed.")
        return 1
    r = http.post(
        conf().steps.analytics.google_token_url,
        data={
            "client_id": s.client_id,
            "client_secret": s.client_secret,
            "code": q["code"][0],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT,
        },
    )
    body = r.json()
    if r.status_code >= 400 or "refresh_token" not in body:
        print(f"Token exchange failed: {body.get('error')} {body.get('error_description', '')}; nothing changed.")
        return 1
    from reelline.config.credentials import YouTubeSettings
    from reelline.publish.youtube import YouTubeClient

    probe = YouTubeClient(
        YouTubeSettings(s.api_key, s.client_id, s.client_secret, body["refresh_token"], s.env_file), http=http
    )
    today = date.today()
    probe._call(
        "auth_probe",
        "GET",
        conf().steps.analytics.youtube_analytics_url,
        params={
            "ids": "channel==MINE",
            "startDate": (today - timedelta(days=7)).isoformat(),
            "endDate": today.isoformat(),
            "metrics": "views",
        },
        headers={"Authorization": f"Bearer {probe.access_token()}"},
    )
    granted = set((body.get("scope") or "").split())
    channel = (
        probe._call(
            "auth_probe_channel",
            "GET",
            f"{conf().steps.analytics.youtube_data_url}/channels",
            params={"part": "id,snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {probe.access_token()}"},
        ).get("items")
        or []
    )
    backup = set_env(s.env_file, {"YOUTUBE_REFRESH_TOKEN": body["refresh_token"]})
    print(
        f"Analytics query OK; channel {channel[0]['snippet']['title'] + ' (' + channel[0]['id'] + ')' if channel else '?'};"
        f" comment scope {'granted' if conf().steps.analytics.youtube_comment_scope in granted else 'MISSING'}. "
        f"YOUTUBE_REFRESH_TOKEN written to {s.env_file} (backup {backup.name})."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m reelline.pipeline.stats_auth", description="One-time stats authorizations"
    )
    p.add_argument("which", choices=("meta", "youtube"))
    args = p.parse_args(argv)
    try:
        return meta() if args.which == "meta" else youtube()
    except AuthError as exc:
        print(f"Failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
