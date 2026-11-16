"""Publishing, the credits pages, the shortlinks, the platform accounts, and storage."""

from __future__ import annotations

from pathlib import Path

from .base import ChannelPath, Section, env

# ---- publish ---------------------------------------------------------------------------------


class CreditsPage(Section):
    """reelline/pipeline/credits.py: one static page per reel on Cloudflare Pages."""

    project: str = ""
    # The project's *.pages.dev subdomain (or a custom domain).
    domain: str = ""
    production_branch: str = "main"
    line: str = "Images: {domain}/c/{reel_id}"
    max_line: int = 4
    wrangler: str = "wrangler@4.146.0"
    deploy_timeout_s: int = 300
    deployment_verify_timeout_s: int = 60
    verify_timeout_s: int = 120
    verify_poll_s: int = 5
    api_url: str = "https://api.cloudflare.com/client/v4"
    colors: dict[str, str] = {
        "yellow": "#FFFFFF",
        "ink": "#000000",
        "night": "#111111",
        "night_2": "#222222",
        "text": "#EEEEEE",
    }
    labels: dict[str, str] = {
        "title": "Title",
        "author": "Author",
        "license": "License",
        "source": "Source",
        "generated": "AI-generated illustration",
        "generated_author": "AI-generated (Gemini)",
        "heading": "Images shown in this video",
        "site": "Credits",
        "unknown": "unknown",
        "manual": "Added by hand",
    }
    # The privacy / data-deletion pages: files <slug>.html here (see pipeline/site_pages.py), the date they show,
    # and the footer's links (path -> text) on every page.
    pages_dir: ChannelPath = Path("site")
    pages_updated: str = "2026-10-02"
    footer: dict[str, str] = {"/privacy": "Privacy", "/data-deletion": "Data deletion"}
    api_token_env: str = env(
        "CLOUDFLARE_API_TOKEN",
        "Cloudflare API token with Account > Cloudflare Pages > Edit "
        "(dash.cloudflare.com > My Profile > API Tokens), same account as R2.",
    )


class Shortlinks(Section):
    """reelline/pipeline/shortlinks.py: <base>/<code> redirect pages on the credits site."""

    # Off: the caption links the source's own URL and no site is deployed for it (a channel without a Cloudflare Pages site).
    enabled: bool = True
    base: str = ""
    code_len: int = 6
    alphabet: str = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    max_tries: int = 50


class Meta(Section):
    app_id_env: str = env("META_APP_ID", "Meta app ID (developers.facebook.com > My Apps).", secret=False)
    app_secret_env: str = env("META_APP_SECRET", "That app's secret (App settings > Basic).")
    page_id_env: str = env(
        "META_PAGE_ID",
        "ID of the Facebook Page the reels go to (checked against publish.facebook_page_id).",
        secret=False,
    )
    page_token_env: str = env(
        "META_PAGE_TOKEN",
        "Never-expiring Page access token (from a long-lived user token) with "
        "pages_manage_posts, pages_read_engagement, pages_manage_engagement, "
        "pages_read_user_content.",
    )


class Buffer(Section):
    api_key_env: str = env("BUFFER_API_KEY", "Buffer API key (publish.buffer.com > Settings > API).")
    org_id_env: str = env("BUFFER_ORG_ID", "Buffer organization ID.", secret=False)
    channel_instagram_env: str = env(
        "BUFFER_CHANNEL_INSTAGRAM", "Buffer channel ID of the Instagram account.", secret=False
    )
    channel_tiktok_env: str = env("BUFFER_CHANNEL_TIKTOK", "Buffer channel ID of the TikTok account.", secret=False)
    channel_youtube_env: str = env("BUFFER_CHANNEL_YOUTUBE", "Buffer channel ID of the YouTube channel.", secret=False)


class R2(Section):
    account_id_env: str = env(
        "R2_ACCOUNT_ID",
        "Cloudflare account ID (dash.cloudflare.com, the account's overview); also the Pages account.",
        secret=False,
    )
    access_key_id_env: str = env(
        "R2_ACCESS_KEY_ID", "R2 API token's S3 access key ID, object read/write on the bucket (R2 > Manage API tokens)."
    )
    secret_access_key_env: str = env("R2_SECRET_ACCESS_KEY", "That token's S3 secret access key.")
    endpoint_env: str = env("R2_ENDPOINT", "S3 endpoint: https://<account id>.r2.cloudflarestorage.com.", secret=False)
    bucket_env: str = env("R2_BUCKET", "The bucket the reel files go to.", secret=False)
    public_url_env: str = env(
        "R2_PUBLIC_URL",
        "The bucket's public r2.dev URL (bucket > Settings > Public access): Buffer fetches the reels from it.",
        secret=False,
        optional=True,
    )


class TiktokBusiness(Section):
    api_url: str = "https://business-api.tiktok.com/open_api/v1.3"
    app_id_env: str = env(
        "TIKTOK_BUSINESS_APP_ID",
        "TikTok API for Business app ID (Accounts API, approved for Account Comment > Manage Account Comment).",
        secret=False,
        optional=True,
    )
    app_secret_env: str = env("TIKTOK_BUSINESS_SECRET", "That app's secret.", optional=True)
    business_id_env: str = env(
        "TIKTOK_BUSINESS_ID", "The authorized account's open_id (its business id).", secret=False, optional=True
    )
    refresh_token_env: str = env("TIKTOK_BUSINESS_REFRESH_TOKEN", "The account's refresh token.", optional=True)


class TiktokPosting(Section):
    """reelline/publish/tiktok.py: the Content Posting API (direct post by URL). Optional credentials."""

    api_url: str = "https://open.tiktokapis.com/v2"
    privacy: str = "PUBLIC_TO_EVERYONE"
    client_key_env: str = env(
        "TIKTOK_CLIENT_KEY", "TikTok developer app's client key (Content Posting API).", secret=False, optional=True
    )
    client_secret_env: str = env("TIKTOK_CLIENT_SECRET", "That app's client secret.", optional=True)
    refresh_token_env: str = env(
        "TIKTOK_REFRESH_TOKEN", "The posting account's refresh token (scope video.publish).", optional=True
    )


class LocalPublish(Section):
    """reelline/publish/local.py: the folder the renders and their JSON sidecars are copied to."""

    dir: ChannelPath = Path("out/published")


class Publish(Section):
    """reelline/pipeline/publish.py (step 7) and the adapters in reelline/publish (buffer, meta, youtube,
    tiktok, local), reelline/storage (r2_key and the r2 credentials), comments.py."""

    enabled: bool = False
    # channel -> the adapter that publishes it (reelline/publish/registry.py): buffer, meta, youtube, tiktok, local.
    targets: dict[str, str] = {"instagram": "buffer", "tiktok": "buffer", "youtube": "buffer", "facebook": "meta"}
    channels: dict[str, bool] = {"instagram": True, "tiktok": True, "youtube": True, "facebook": True}
    labels: dict[str, str] = {
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "facebook": "Facebook",
    }
    max_mb: int = 200
    duration_s: tuple[float, float] = (3.0, 90.0)
    size_px: tuple[int, int] = (1080, 1920)
    video_codec: str = "h264"
    fps: tuple[int, int] = (24, 60)
    buffer_lead_h: int = 26
    url_after_slot_h: int = 24
    url_max_days: int = 7
    r2_key: str = "reels/{reel_id}-{token}/{file}"
    now_within_min: int = 15
    buffer_url: str = "https://api.buffer.com"
    thumbnail_offset_ms: int = 1500
    instagram_first_comment: bool = False
    ai_generated: bool = True
    youtube: dict[str, str | bool] = {
        "categoryId": "27",
        "privacy": "public",
        "madeForKids": False,
        "notifySubscribers": True,
    }
    graph_url: str = "https://graph.facebook.com/v26.0"
    # The Facebook Page, checked by id and name before every run.
    facebook_page_id: str = ""
    facebook_page_name: str = ""
    facebook_upload_wait_s: int = 600
    facebook_upload_poll_s: int = 5
    facebook_data_access_warn_days: int = 14
    poll_interval_min: int = 5
    poll_timeout_h: int = 3
    unschedule_until_min: int = 5
    http_timeout_s: int = 60
    upload_timeout_s: int = 600
    live_test_ahead_h: int = 48
    live_poll_s: int = 30
    buttons: dict[str, str] = {
        "retry": "🔁 Retry {channel}",
        "retry_all": "🔁 Retry all",
        "unschedule": "↩️ Unschedule",
        "retry_comment": "🔁 Retry {channel} comment",
    }
    comments: dict[str, bool] = {"instagram": False, "tiktok": False, "youtube": False, "facebook": False}
    comment_lines: dict[str, str] = {"source": "Source: {url}", "credits": "Images: {url}"}
    # The credit line of an image by its source, and of a sound (media/images/licenses.py, media/sfx/credits.py).
    credit_lines: dict[str, str] = {
        "commons": "Image: {author}, {license} - Wikimedia Commons",
        "pexels": "Photo: {author} / Pexels",
        "pixabay": "Photo: {author} / Pixabay",
        "manual": "Image: {author}, {license}",
        "sfx": "Sound: {author} / {site}",
    }
    comment_wait_min: int = 60
    credits_page: CreditsPage = CreditsPage()
    shortlinks: Shortlinks = Shortlinks()
    meta: Meta = Meta()
    buffer: Buffer = Buffer()
    r2: R2 = R2()
    tiktok_business: TiktokBusiness = TiktokBusiness()
    tiktok: TiktokPosting = TiktokPosting()
    local: LocalPublish = LocalPublish()
    youtube_upload_url: str = "https://www.googleapis.com/upload/youtube/v3"


# ---- storage ---------------------------------------------------------------------------------


class Blob(Section):
    """reelline/storage: where the files the platforms fetch wait. s3 = the S3 API (Cloudflare R2: the
    credentials are publish.r2's); local = a folder."""

    kind: str = "s3"
    local_dir: ChannelPath = Path("out/blobs")
    local_base_url: str | None = None


class Storage(Section):
    blob: Blob = Blob()
    # The state database (SQLite; migrations run on every open) and its lock wait.
    db_path: ChannelPath = Path("data/reelline.db")
    db_busy_timeout_s: float = 10.0
    prompts_dir: ChannelPath = Path("prompts")
    briefs_dir: ChannelPath = Path("content/briefs")
    # Final scripts written by the script step (the scripts table is the record).
    scripts_dir: ChannelPath = Path("content/reels/auto")
    # Hook winners/flops for the story prompt, written weekly by reelline/pipeline/retention.py.
    hook_examples: ChannelPath = Path("data/hook_examples.json")
    jobs_log_dir: ChannelPath = Path("out/jobs")
    publish_test_dir: ChannelPath = Path("out/publish_test")
    credits_site_dir: ChannelPath = Path("out/credits_site")
    log_dir: ChannelPath = Path("logs")
    # The env file with the credentials. None: <channel>/.env if it exists, else <repo>/.env.
    env_file: ChannelPath | None = None
