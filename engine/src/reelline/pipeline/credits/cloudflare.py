"""Cloudflare Pages: project, wrangler deploy, and the live checks."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from reelline.config import conf
from reelline.pipeline.credits.base import CreditsError, page_url


def _api(http, settings, method: str, path: str, **kw) -> tuple[int, dict]:
    r = http.request(
        method,
        f"{conf().publish.credits_page.api_url}/accounts/{settings.account_id}{path}",
        headers={"Authorization": f"Bearer {settings.api_token}"},
        **kw,
    )
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def ensure_project(http, settings, log: Callable[[str], None] = print) -> str:
    """The Pages project (created when missing); its *.pages.dev subdomain."""
    name = conf().publish.credits_page.project
    status, body = _api(http, settings, "GET", f"/pages/projects/{name}")
    if status == 404:
        status, body = _api(
            http,
            settings,
            "POST",
            "/pages/projects",
            json={"name": name, "production_branch": conf().publish.credits_page.production_branch},
        )
        if status >= 300 or not body.get("success"):
            raise CreditsError(f"Pages project {name}: create failed: HTTP {status} {body.get('errors')}")
        log(f"Pages project {name} created: {body['result'].get('subdomain')}")
    elif status >= 300 or not body.get("success"):
        raise CreditsError(f"Pages project {name}: HTTP {status} {body.get('errors')}")
    subdomain = body["result"].get("subdomain")
    if subdomain != conf().publish.credits_page.domain:
        raise CreditsError(
            f"Pages project {name} is at {subdomain}, but captions link {conf().publish.credits_page.domain}: "
            "set publish.credits_page.domain"
        )
    return subdomain


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_DEPLOYMENT_URL = re.compile(r"Take a peek over at (https://\S+)")


def _clean(text, token: str) -> str:
    """wrangler's output as text: no colour codes, no token."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = _ANSI.sub("", text or "").strip()
    return text.replace(token, "<token>") if token else text


def wrangler_deploy(site_dir: Path, settings, runner=subprocess.run) -> str:
    """`wrangler pages deploy` to the production branch; its output. A failure (an exit
    code, a timeout) raises CreditsError with wrangler's stderr in it."""
    env = {
        **os.environ,
        "CLOUDFLARE_API_TOKEN": settings.api_token,
        "CLOUDFLARE_ACCOUNT_ID": settings.account_id,
        "WRANGLER_SEND_METRICS": "false",
    }
    cmd = [
        "npx",
        "--yes",
        conf().publish.credits_page.wrangler,
        "pages",
        "deploy",
        str(site_dir),
        "--project-name",
        conf().publish.credits_page.project,
        "--branch",
        conf().publish.credits_page.production_branch,
        "--commit-dirty=true",
    ]
    try:
        r = runner(cmd, env=env, capture_output=True, text=True, timeout=conf().publish.credits_page.deploy_timeout_s)
    except subprocess.TimeoutExpired as exc:
        stderr = _clean(exc.stderr, settings.api_token)
        raise CreditsError(
            f"wrangler pages deploy: no answer in {conf().publish.credits_page.deploy_timeout_s}s; stderr: "
            f"{stderr[-800:] or '(empty)'}"
        ) from None
    except OSError as exc:
        raise CreditsError(f"wrangler pages deploy: {type(exc).__name__}: {exc}") from None
    stdout, stderr = _clean(r.stdout, settings.api_token), _clean(r.stderr, settings.api_token)
    if r.returncode != 0:
        raise CreditsError(
            f"wrangler pages deploy exited {r.returncode}; stderr: {stderr[-800:] or '(empty)'}"
            + (f"; stdout: {stdout[-300:]}" if stdout else "")
        )
    return f"{stdout}\n{stderr}".strip()


def deployment_url(output: str | None) -> str | None:
    """The new deployment's own address in wrangler's output ("Take a peek over at
    https://<id>.<project>.pages.dev"): it serves that deployment, and only it, once wrangler
    is done; None when the output doesn't name one."""
    m = _DEPLOYMENT_URL.search(output or "")
    return m.group(1).rstrip("/") if m else None


def production_deployment(http, settings) -> dict | None:
    """The deployment Cloudflare serves as the project's production (its
    canonical_deployment: id, url, environment, deployment_trigger); None when the API
    doesn't say."""
    try:
        status, body = _api(http, settings, "GET", f"/pages/projects/{conf().publish.credits_page.project}")
    except Exception:  # network: only a hint, never the reason a publish fails
        return None
    if status >= 300 or not body.get("success"):
        return None
    return (body.get("result") or {}).get("canonical_deployment") or None


def check_live(
    reel_id: int,
    http,
    sleep: Callable[[float], None] = time.sleep,
    base: str | None = None,
    timeout_s: float | None = None,
) -> str:
    """The reel's page answers 200 with its marker, at its caption address or, with
    `base`, on that deployment's own address."""
    url = f"{base}/c/{reel_id}" if base else page_url(reel_id)
    return check_url(url, f'name="{conf().channel.name}-reel" content="{reel_id}"', http, sleep, timeout_s)


def check_url(
    url: str, marker: str, http, sleep: Callable[[float], None] = time.sleep, timeout_s: float | None = None
) -> str:
    """`url` answers 200 (redirects followed) with `marker` in the page, within
    `timeout_s` (publish.credits_page.verify_timeout_s; 0: one look)."""
    timeout_s = conf().publish.credits_page.verify_timeout_s if timeout_s is None else timeout_s
    waited, last = 0.0, None
    while True:
        try:
            r = http.get(url, follow_redirects=True, timeout=30)
            if r.status_code == 200 and marker in r.text:
                return url
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # network
            last = f"{type(exc).__name__}: {exc}"
        if waited >= timeout_s:
            raise CreditsError(f"{url} not live after {waited:.0f}s ({last})")
        sleep(conf().publish.credits_page.verify_poll_s)
        waited += conf().publish.credits_page.verify_poll_s


def check_pages(
    conn: sqlite3.Connection,
    reel_id: int,
    shown: bool,
    links: list,
    http,
    sleep: Callable[[float], None] = time.sleep,
    base: str | None = None,
    timeout_s: float | None = None,
) -> dict:
    """The reel's credits page (`shown`) and each of its links served right, where
    captions link them or, with `base`, on that deployment's own address (no verified_at
    recorded then). {"credits": URL or None, "links": [URLs]}; CreditsError or
    ShortlinkError on the first that isn't."""
    from reelline.pipeline import shortlinks

    out: dict = {"credits": check_live(reel_id, http, sleep, base, timeout_s) if shown else None, "links": []}
    for link in links:
        out["links"].append(
            shortlinks.check_link(
                None if base else conn, link["code"], link["target"], http, sleep, base=base, timeout_s=timeout_s
            )
        )
    return out


def not_live_text(problem: str, deployment: str | None, http, settings) -> str:
    """Why the caption address doesn't serve what was deployed, as far as Cloudflare's
    API tells: which deployment it calls production."""
    domain = conf().publish.credits_page.domain
    prod = production_deployment(http, settings)
    if prod is None:
        return f"{problem}; Cloudflare's API didn't say which deployment is production"
    url = (prod.get("url") or "").rstrip("/")
    if deployment and url == deployment:
        return (
            f"{problem}, though deployment {deployment} serves it and Cloudflare lists it as production: "
            f"{domain} still serves an older one (Cloudflare hasn't switched it over; nothing to redeploy). "
            "Retry all once the page opens"
        )
    branch = ((prod.get("deployment_trigger") or {}).get("metadata") or {}).get("branch")
    return (
        f"{problem}; Cloudflare's production deployment is {url or prod.get('id')} (branch {branch}, "
        f"{prod.get('environment')}), not {deployment or 'the new one (wrangler named no URL)'}"
    )
