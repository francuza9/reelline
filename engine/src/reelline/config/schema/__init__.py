"""The channel config's schema: every setting the engine reads, typed, with generic defaults.

A channel's config.yaml sets what differs from these (the first channel's config.yaml sets everything, with the why of each value). Grouped by role:

  channel   who the channel is: name, its user agent, local time, publish slots
  models    the model providers, one role per use (writer, checker, vision, tts, ...), their
            prices, and the retry of a dropped connection
  sources   the sources registry file (sources.yaml)
  pipeline  the steps a run goes through, in order (reelline/pipeline/chain.py)
  steps     one section per pipeline step (enabled, use, its settings), plus the settings sections
            some steps share (script, endcard, produce, run_daily, retention, analytics)
  review    the review chat (telegram) and the approval bot (approve)
  publish   publishing, the image credits pages, the shortlinks, the platform accounts
  storage   where state lives: the database, briefs, scripts, logs, the env file
  costs     the cost report's labels and groupings
  render    the reel render's settings and media-source accounts

Paths are relative to the channel directory (resolved when loaded). Credentials are never here:
a `*_env` field names the variable in the env file that holds the value (reelline/config/credentials.py).
"""

from __future__ import annotations

from pathlib import Path

from .base import ChannelPath, Number, Section, env
from .channel import (
    Approve,
    Channel,
    Notify,
    NotifyLog,
    NotifySlack,
    Review,
    ReviewAuto,
    ReviewCli,
    ReviewWebhook,
    Telegram,
)
from .costs import Costs, Freesound, Pexels, Pixabay, Render
from .models import (
    LLM_ROLES,
    AnthropicConf,
    GoogleConf,
    ImageRole,
    ModelRole,
    Models,
    OpenAIConf,
    ProviderConf,
    Providers,
    Retry,
    TtsRole,
)
from .publish import (
    R2,
    Blob,
    Buffer,
    CreditsPage,
    LocalPublish,
    Meta,
    Publish,
    Shortlinks,
    Storage,
    TiktokBusiness,
    TiktokPosting,
)
from .steps import DEFAULT_PIPELINE, Analytics, Produce, Retention, RunDaily, Steps, YoutubeApi
from .steps_pipeline import Brief, Caption, CustomStep, Endcard, Factcheck, Ingest, Pick, Proofread, Script, StepConf

__all__ = [
    "DEFAULT_PIPELINE",
    "LLM_ROLES",
    "R2",
    "Analytics",
    "AnthropicConf",
    "Approve",
    "Blob",
    "Brief",
    "Buffer",
    "Caption",
    "Channel",
    "ChannelPath",
    "Config",
    "Costs",
    "CreditsPage",
    "CustomStep",
    "Endcard",
    "Factcheck",
    "Freesound",
    "GoogleConf",
    "ImageRole",
    "Ingest",
    "LocalPublish",
    "Meta",
    "ModelRole",
    "Models",
    "Notify",
    "NotifyLog",
    "NotifySlack",
    "Number",
    "OpenAIConf",
    "Pexels",
    "Pick",
    "Pixabay",
    "Produce",
    "Proofread",
    "ProviderConf",
    "Providers",
    "Publish",
    "Render",
    "Retention",
    "Retry",
    "Review",
    "ReviewAuto",
    "ReviewCli",
    "ReviewWebhook",
    "RunDaily",
    "Script",
    "Section",
    "Shortlinks",
    "StepConf",
    "Steps",
    "Storage",
    "Telegram",
    "TiktokBusiness",
    "TiktokPosting",
    "TtsRole",
    "YoutubeApi",
    "env",
]


class Config(Section):
    channel: Channel = Channel()
    models: Models = Models()
    # The sources registry (validated by reelline/pipeline/sources.py load_sources()).
    sources: ChannelPath = Path("sources.yaml")
    pipeline: tuple[str, ...] = DEFAULT_PIPELINE
    steps: Steps = Steps()
    review: Review = Review()
    notify: Notify = Notify()
    publish: Publish = Publish()
    storage: Storage = Storage()
    costs: Costs = Costs()
    render: Render = Render()
