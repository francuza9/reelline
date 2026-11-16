"""The static pages next to the credits pages on the credits site (credits/pages.py build_site()): the privacy
policy and the data deletion instructions a Meta app needs, served at /privacy and /data-deletion (Cloudflare
Pages drops the .html). A channel's own pages are files in publish.credits_page.pages_dir
(<slug>.html: the title on the first line, then the body, trusted HTML; {OPERATOR}, {APP_NAME}, {MAIL} and
{UPDATED} are filled in); without a file the page is the plain English one below."""

from __future__ import annotations

from reelline.config import conf

PRIVACY_EN = """
<h1>Privacy Policy</h1>
<p class="sub">{OPERATOR} · last updated {UPDATED}</p>
<h2>Who we are</h2>
<p>This site and the app "{APP_NAME}" are operated by {OPERATOR}. Contact: {MAIL}.</p>
<h2>What the app does</h2>
<p>The app only publishes {OPERATOR}'s own videos to {OPERATOR}'s own Facebook Page, and reads that Page's comments
and statistics so we can see how our videos are received.</p>
<h2>What data we collect</h2>
<p>The app collects no data from other users. It asks no one to log in, sets no cookies and does no tracking.</p>
<h2>Sharing</h2>
<p>We do not sell any data, and we do not share any data with anyone.</p>
<h2>Data deletion</h2>
<p>See the <a href="/data-deletion">data deletion instructions</a>.</p>
"""

DELETION_EN = """
<h1>Data Deletion</h1>
<p class="sub">{OPERATOR} · last updated {UPDATED}</p>
<h2>How to request deletion</h2>
<p>Email {MAIL} with the subject "Data deletion". Include your name on Facebook and, if you can, a link to the
comment or content your request is about.</p>
<h2>What gets deleted</h2>
<p>Any information about you that {OPERATOR} holds through this app. The app stores no data about other users, so
usually there is nothing to delete, and we will confirm that to you in writing. On request we also delete your
comments from {OPERATOR}'s Facebook Page.</p>
<h2>Response time</h2>
<p>We confirm your request within 7 days and complete the deletion within 30 days, then tell you the result.</p>
"""

_DEFAULTS = {"privacy": ("Privacy Policy", PRIVACY_EN), "data-deletion": ("Data Deletion", DELETION_EN)}


def pages() -> dict[str, tuple[str, str]]:
    """path (served without .html) -> (title, body)."""
    cfg = conf()
    mail = f'<a href="mailto:{cfg.channel.contact}">{cfg.channel.contact}</a>'
    fills = {
        "{OPERATOR}": cfg.channel.operator,
        "{APP_NAME}": cfg.channel.app_name,
        "{MAIL}": mail,
        "{UPDATED}": cfg.publish.credits_page.pages_updated,
    }
    found = {}
    for slug, (title, body) in _DEFAULTS.items():
        path = cfg.publish.credits_page.pages_dir / f"{slug}.html"
        if path.is_file():
            title, _, body = path.read_text(encoding="utf-8").partition("\n")
        for token, value in fills.items():
            body = body.replace(token, value)
        found[slug] = (title, body)
    return found
