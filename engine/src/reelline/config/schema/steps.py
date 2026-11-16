"""Produce, run_daily, retention, analytics and the Steps section (one per pipeline step)."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, model_validator

from .base import Number, Section, env
from .steps_pipeline import (
    Brief,
    Caption,
    CustomStep,
    Endcard,
    Factcheck,
    Ingest,
    Pick,
    Proofread,
    Qa,
    Script,
    StepConf,
)


class Produce(Section):
    """reelline/pipeline/produce.py: scripted pick -> reel + cover -> review."""

    default_mood: str = "smug"
    render_attempts: int = 2
    pick_statuses: tuple[str, ...] = ("scripted", "render_failed")
    script_statuses: tuple[str, ...] = ("ok", "flagged")
    error_chars: int = 3500


class RunDaily(Section):
    """reelline/pipeline/run_daily.py: the tick, the evening batch, the chains, the bot's supervision."""

    tick_every_min: int = 10
    thinking_alert_days: int = 2
    ingest_every_h: int = 3
    ingest_skip_if_ran_min: int = 60
    # Slots dated this or later run in the evening batch (None: every slot runs same-day).
    evening_batch_from: str | None = None
    evening_start_at: str = "17:30"
    evening_delivery_at: str = "20:00"
    chain_start_before_h: int = 5
    catch_up_until_before_h: int = 1
    alert_no_review_before_h: int = 2
    max_resumes: int = 2
    step_max_min: dict[str, int] = {
        "pick": 20,
        "script": 30,
        "produce": 45,
        "qa": 20,
        "drop the pick": 10,
        "pick another story": 20,
        "publish": 30,
        "unschedule": 15,
    }
    step_max_default_min: int = 45
    wait_alert_after_min: int = 45
    bot_heartbeat_stale_min: int = 3
    log_keep_days: int = 14
    test_override: dict[str, str] | None = None
    # Who restarts a dead approval bot on this machine: the tick, or systemd (the server).
    # Per machine: set it with REELLINE__STEPS__RUN_DAILY__BOT_SUPERVISOR (process env or the env file).
    bot_supervisor: Literal["tick", "systemd"] = "tick"


class Retention(Section):
    """reelline/pipeline/retention.py."""

    line_after_h: int = 72
    line_grace_h: int = 12
    line_window_h: int = 48
    min_curve_target_h: int = 72
    curve_points: int = 100
    half_gone_share: float = 0.5
    examples_weekday: int = 6
    examples_at: str = "16:00"
    examples_days: int = 30
    examples_min_age_h: int = 72
    examples_min_views: int = 100
    examples_platform_min_views: int = 50
    examples_top: int = 3
    examples_half_gone_weight: float = 0.3


class YoutubeApi(Section):
    api_key_env: str = env(
        "YOUTUBE_API_KEY",
        "YouTube Data API v3 key (console.cloud.google.com > APIs & Services > "
        "Credentials), restricted to that API: views, likes, comments, subscribers.",
        optional=True,
    )
    client_id_env: str = env(
        "YOUTUBE_CLIENT_ID",
        "OAuth client ID (type Desktop app) for the YouTube Analytics API; the consent screen must be In production.",
        secret=False,
        optional=True,
    )
    client_secret_env: str = env("YOUTUBE_CLIENT_SECRET", "That OAuth client's secret.", optional=True)
    refresh_token_env: str = env(
        "YOUTUBE_REFRESH_TOKEN",
        "The channel owner's refresh token: written by `python -m reelline.pipeline.stats_auth youtube`.",
        optional=True,
    )


class Analytics(Section):
    """reelline/pipeline/stats.py fetches, reelline/pipeline/analytics.py reports (read-only)."""

    snapshots_h: tuple[int, ...] = (1, 24, 72, 168) + tuple(24 * d for d in range(8, 15))
    weekly_after_days: int = 14
    weekly_until_days: int = 365
    sweep_at: str = "19:30"
    buffer_margins: dict[str, int] = {"15min": 20, "1day": 50, "30days": 500}
    alert_after_failures: int = 3
    http_timeout_s: float = 20.0
    youtube_data_url: str = "https://www.googleapis.com/youtube/v3"
    youtube_analytics_url: str = "https://youtubeanalytics.googleapis.com/v2/reports"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    youtube_scope: str = "https://www.googleapis.com/auth/yt-analytics.readonly"
    youtube_comment_scope: str = "https://www.googleapis.com/auth/youtube.force-ssl"
    # The Google Cloud project of the OAuth client (named in stats_auth's instructions).
    google_project: str = ""
    meta_insights_scope: str = "read_insights"
    meta_ig_scopes: tuple[str, ...] = ("instagram_basic", "instagram_manage_insights")
    platforms: tuple[str, ...] = ("instagram", "tiktok", "youtube", "facebook")
    # The stats collectors of a round, in fetch order (reelline/stats: buffer, meta, youtube, tiktok).
    collectors: tuple[str, ...] = ("buffer", "meta", "youtube")
    labels: dict[str, str] = {
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "facebook": "Facebook",
    }
    short: dict[str, str] = {"instagram": "IG", "tiktok": "TT", "youtube": "YT", "facebook": "FB"}
    fair_age_h: int = 24
    fair_window_h: tuple[int, int] = (12, 36)
    watch_min_views: int = 50
    replay_mark: str = "↻"
    min_split_reels: int = 3
    opening_min_reels: int = 3
    report_slots: tuple[str, ...] = ("20:00",)
    slot_tolerance_min: int = 90
    other_slot: str = "other"
    topics: tuple[str, ...] = ("education", "startups", "tech")
    top_n: int = 5
    title_chars: int = 40
    periods: dict[str, str] = {"7": "Last 7 days", "30": "Last 30 days", "all": "All time"}
    buttons: dict[str, str] = {"7": "Last 7 days", "30": "Last 30 days", "all": "All time"}
    curve_h: tuple[tuple[int, str], ...] = ((1, "1h"), (24, "24h"), (72, "72h"), (168, "7d"))
    curve_windows_h: dict[int, tuple[Number, Number]] = {1: (0.5, 3), 72: (54, 108), 168: (126, 252)}
    needs: dict[str, str] = {
        "facebook_insights": "read_insights: re-authorize the Meta token",
        "instagram_graph": "instagram_basic + instagram_manage_insights and IG linked to the Page: re-authorize Meta",
        "youtube_data": "YOUTUBE_API_KEY in .env",
        "youtube_analytics": "YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN in .env: python -m reelline.pipeline.stats_auth youtube",
        "tiktok_followers": "not in Buffer's API; TikTok's own API needs app review",
    }
    youtube_api: YoutubeApi = YoutubeApi()


class Steps(Section):
    """One section per pipeline step: its settings, enabled and use (StepConf). A name not listed
    here is a channel's own step (CustomStep, with its use: path). script, endcard, produce,
    run_daily, retention and analytics are settings of the steps that read them (write and style,
    cards, render, tick, rank, stats), not steps of their own."""

    model_config = ConfigDict(extra="allow", validate_default=True, revalidate_instances="always")

    ingest: Ingest = Ingest()
    assess: StepConf = StepConf()
    pick: Pick = Pick()
    brief: Brief = Brief()
    write: StepConf = StepConf()
    style: StepConf = StepConf()
    proofread: Proofread = Proofread()
    factcheck: Factcheck = Factcheck()
    cards: StepConf = StepConf()
    fx: StepConf = StepConf()
    caption: Caption = Caption()
    save: StepConf = StepConf()
    render: StepConf = StepConf()
    qa: Qa = Qa()
    review: StepConf = StepConf()
    publish: StepConf = StepConf()
    stats: StepConf = StepConf()
    rank: StepConf = StepConf()
    script: Script = Script()
    endcard: Endcard = Endcard()
    produce: Produce = Produce()
    run_daily: RunDaily = RunDaily()
    retention: Retention = Retention()
    analytics: Analytics = Analytics()

    @model_validator(mode="after")
    def _custom_steps(self) -> Steps:
        extra = self.__pydantic_extra__ or {}
        for name, value in extra.items():
            if not isinstance(value, CustomStep):
                if not isinstance(value, dict):
                    raise ValueError(f"steps.{name} must be a mapping (enabled, use, its settings)")
                extra[name] = CustomStep(**value)
        return self

    def get(self, name: str) -> StepConf | None:
        """A step's section (a channel's own step's too), or None."""
        value = (
            getattr(self, name, None) if name in type(self).model_fields else (self.__pydantic_extra__ or {}).get(name)
        )
        return value if isinstance(value, StepConf) else None


# The steps a run goes through, in order (reelline/pipeline/chain.py validates it on load).
DEFAULT_PIPELINE = (
    "ingest",
    "assess",
    "pick",
    "brief",
    "write",
    "style",
    "proofread",
    "factcheck",
    "cards",
    "fx",
    "caption",
    "save",
    "render",
    "qa",
    "review",
    "publish",
    "stats",
    "rank",
)
