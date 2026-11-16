"""Publishing: a reel's credits page and links live before it posts."""

from __future__ import annotations

import sqlite3
import subprocess
import time
from collections.abc import Callable

from reelline.config import conf
from reelline.paths import stored_path
from reelline.pipeline import credits as _c
from reelline.pipeline import db
from reelline.pipeline.credits.base import CreditsError, shown_images
from reelline.pipeline.credits.cloudflare import (
    check_pages,
    check_url,
    deployment_url,
    not_live_text,
    production_deployment,
)
from reelline.pipeline.credits.pages import build_site
from reelline.pipeline.credits.snapshot import snapshot


def publish_page(conn: sqlite3.Connection, reel_id: int, **kw) -> str:
    """The reel's credits page live, its links too (publish_site()); its URL."""
    reel = conn.execute("SELECT images_path FROM reels WHERE id = ?", (reel_id,)).fetchone()
    if not (shown_images(stored_path(reel["images_path"])) if reel and reel["images_path"] else []):
        raise CreditsError(f"Reel #{reel_id} shows no images: nothing to credit")
    return publish_site(conn, reel_id, **kw)["credits"]


def publish_site(
    conn: sqlite3.Connection,
    reel_id: int,
    *,
    settings=None,
    http=None,
    runner=subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict:
    """Before a reel posts: its credits page (when it shows images: snapshotted if not
    yet) and its shortlinks live where captions link them, with at most one deploy.
    {"credits": the page URL or None, "links": [live link URLs]}; {} with no deploy when
    the reel has neither.

    Live already (a retry): no deploy. Cloudflare's production deployment has them already
    (a retry while it still switches over): no new deploy either, only the wait. Else the
    whole site is built and deployed, and the new deployment's own address
    (deployment_url()) must serve them within publish.credits_page.deployment_verify_timeout_s
    (else the build is wrong). Then the caption addresses must, within
    publish.credits_page.verify_timeout_s; else the error says which deployment Cloudflare
    calls production (not_live_text())."""
    import httpx

    from reelline.config.credentials import load_cloudflare
    from reelline.core.errors import ConfigError
    from reelline.pipeline import shortlinks
    from reelline.pipeline.shortlinks import ShortlinkError

    reel = conn.execute("SELECT images_path FROM reels WHERE id = ?", (reel_id,)).fetchone()
    shown = shown_images(stored_path(reel["images_path"])) if reel and reel["images_path"] else []
    links = shortlinks.reel_links(conn, reel_id)
    if not shown and not links:
        return {}
    try:
        settings = settings or load_cloudflare()
    except ConfigError as exc:
        raise CreditsError(str(exc)) from None
    http = http or httpx.Client(timeout=60)
    if shown:
        listed = [im["image_id"] for im in snapshot(conn, reel_id)]
        if listed != shown:
            raise CreditsError(f"Reel #{reel_id}: the credits snapshot lists {listed}, but the reel shows {shown}")
    domain = conf().publish.credits_page.domain
    try:
        out = check_pages(conn, reel_id, bool(shown), links, http, sleep, timeout_s=0)
        log(f"Reel #{reel_id}'s pages are live on {domain} already: no deploy")
    except (CreditsError, ShortlinkError):
        _c.ensure_project(http, settings, log)
        deployment = ((production_deployment(http, settings) or {}).get("url") or "").rstrip("/") or None
        try:
            if deployment is None:
                raise CreditsError("no production deployment")
            check_pages(conn, reel_id, bool(shown), links, http, sleep, base=deployment, timeout_s=0)
            log(
                f"Production deployment {deployment} has reel #{reel_id}'s pages already: no new deploy, "
                f"waiting for {domain}"
            )
        except (CreditsError, ShortlinkError):
            build_site(conn)
            deployment = deployment_url(_c.wrangler_deploy(conf().storage.credits_site_dir, settings, runner))
            if deployment is None:
                log(f"wrangler named no deployment URL: checking {domain} only")
            else:
                try:
                    check_pages(
                        conn,
                        reel_id,
                        bool(shown),
                        links,
                        http,
                        sleep,
                        base=deployment,
                        timeout_s=conf().publish.credits_page.deployment_verify_timeout_s,
                    )
                except (CreditsError, ShortlinkError) as exc:
                    raise CreditsError(
                        f"the new deployment doesn't serve reel #{reel_id}'s pages ({exc}): the site build is wrong"
                    ) from None
        try:
            out = check_pages(conn, reel_id, bool(shown), links, http, sleep)
        except (CreditsError, ShortlinkError) as exc:
            raise CreditsError(not_live_text(str(exc), deployment, http, settings)) from None
    if out["credits"]:
        with db.transaction(conn):
            conn.execute(
                "UPDATE credits SET deployed_at = ?, url = ? WHERE reel_id = ?", (db.now_iso(), out["credits"], reel_id)
            )
        log(f"Credits page live: {out['credits']}")
    for address, link in zip(out["links"], links, strict=False):
        log(f"Link live: {address} -> {link['target']}")
    return out


def deploy_site(
    conn: sqlite3.Connection,
    *,
    settings=None,
    http=None,
    runner=subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Build and deploy the whole site (no new snapshot); check the static pages are live."""
    import httpx

    from reelline.config.credentials import load_cloudflare
    from reelline.core.errors import ConfigError
    from reelline.pipeline.site_pages import pages

    try:
        settings = settings or load_cloudflare()
    except ConfigError as exc:
        raise CreditsError(str(exc)) from None
    http = http or httpx.Client(timeout=60)
    _c.ensure_project(http, settings, log)
    build_site(conn)
    _c.wrangler_deploy(conf().storage.credits_site_dir, settings, runner)
    urls = [
        check_url(
            f"https://{conf().publish.credits_page.domain}/{name}",
            f'name="{conf().channel.name}-page" content="{name}"',
            http,
            sleep,
        )
        for name in pages()
    ]
    for url in urls:
        log(f"Live: {url}")
    return urls
