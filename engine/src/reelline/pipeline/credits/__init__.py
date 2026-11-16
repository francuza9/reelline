"""Image credits pages: one static page per reel at https://<domain>/c/<reel id> on
Cloudflare Pages (publish.credits_page in the channel config), instead of a credit line per
image in the caption.

Usage:
  python -m reelline.pipeline.credits deploy-site
      Build and deploy the site as it is; check /privacy and /data-deletion are live
      (reelline/pipeline/site_pages.py: the Meta app's privacy policy and data deletion pages).
  python -m reelline.pipeline.credits build
      Render the whole site into storage.credits_site_dir (no network).
  python -m reelline.pipeline.credits deploy <reel_id>
      The reel's credits page and links live as before posting (publish_site()):
      snapshot (if not yet), build, deploy (unless live already), check.

A reel's images are snapshotted once (the credits table, migration 13) from its
images.json and the image library: every image the reel shows, whatever its license
(title: Commons the file name, Pixabay and Pexels the photo's slug; author, linked when the source
gives a page, publish.credits_page.labels["generated_author"] for an AI illustration;
license, linked; source link; whether attribution is required). The snapshot is what
the page shows from then on,
so a later render of the same script (which rewrites the reel dir) can't change a
published page. Every deploy renders every snapshotted reel: a Pages deployment is a
full snapshot of the site, so the pages stay up for good.

The same site serves the shortlinks (reelline/pipeline/shortlinks.py): one page per code at
/s/<code>, every link ever made, in every deploy.

Deploy: the Pages project must exist with publish.credits_page.domain as its subdomain
(created on first use; a different subdomain stops it, so a caption never links to
the wrong place), then `wrangler pages deploy` (Cloudflare's CLI, pinned) with
CLOUDFLARE_API_TOKEN and the account in its environment (a failure's error carries
wrangler's stderr), then the page must answer 200 with the reel's marker: first on the
new deployment's own address (https://<id>.<project>.pages.dev, from wrangler's output: is
the build right?), then where captions link it (has Cloudflare made that deployment
production?). reelline/pipeline/publish.py runs publish_site() before posting whenever the reel
shows an image or has a shortlink: the credits page and each of the reel's links checked
live, with one deploy at most and none when they're live already or production's
deployment has them (a retry); it stops posting when that raises CreditsError (a
snapshot that doesn't list exactly the images the reel shows raises it too). When the
caption address lags behind, the error says which deployment Cloudflare calls production.

The rule (caption_problems()): a reel that shows any image on the TV (shown_images():
images.json entries with status "image") has the caption line (caption_line()) on
caption line publish.credits_page.max_line or above; a reel that shows none has no line and
no page. reelline/pipeline/caption.py assemble_caption() follows it; reelline/pipeline/qa.py fails a
caption that breaks it (the review's QA and a caption edit's recheck), and
reelline/pipeline/publish.py guard() refuses to post one.

Layout: base (the rule), snapshot, pages (the static site), cloudflare (project, wrangler, live
checks), deploy (publish_site, deploy_site), cli. Cloudflare calls made from deploy.py go through
this package's namespace (ensure_project, wrangler_deploy), so tests can patch them here.
"""

from __future__ import annotations

from reelline.pipeline.credits.base import (
    SOURCE_NAMES,
    CreditsError,
    caption_line,
    caption_problems,
    page_url,
    shown_images,
)
from reelline.pipeline.credits.cli import main
from reelline.pipeline.credits.cloudflare import (
    check_live,
    check_pages,
    check_url,
    deployment_url,
    ensure_project,
    not_live_text,
    production_deployment,
    wrangler_deploy,
)
from reelline.pipeline.credits.deploy import deploy_site, publish_page, publish_site
from reelline.pipeline.credits.pages import STYLE, build_site, page_html
from reelline.pipeline.credits.snapshot import commons_author, image_title, snapshot, snapshot_images

__all__ = [
    "SOURCE_NAMES",
    "STYLE",
    "CreditsError",
    "build_site",
    "caption_line",
    "caption_problems",
    "check_live",
    "check_pages",
    "check_url",
    "commons_author",
    "deploy_site",
    "deployment_url",
    "ensure_project",
    "image_title",
    "main",
    "not_live_text",
    "page_html",
    "page_url",
    "production_deployment",
    "publish_page",
    "publish_site",
    "shown_images",
    "snapshot",
    "snapshot_images",
    "wrangler_deploy",
]
