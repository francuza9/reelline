"""The channel, review and notify sections."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .base import ChannelPath, Section, env

# ---- channel ---------------------------------------------------------------------------------


class Channel(Section):
    # The channel's name (the directory's name when not set).
    name: str = ""
    # What the channel is called to people (bot messages, prompts): the name when not set.
    display_name: str = ""
    # Who runs the channel, a contact address and the publishing app's name (the privacy and data-deletion pages,
    # the User-Agent's contact).
    operator: str = ""
    contact: str = ""
    app_name: str = ""
    # The Remotion component (under remotion/src) that draws the mascot: its constants give the face's place.
    mascot_component: str = "Mascot.tsx"
    # The sample caption (two lines of words) and cover headline of render_reel.py --background-check.
    background_check_lines: tuple[tuple[str, ...], ...] = (
        ("Checking", "the", "background,"),
        ("while", "you", "watch."),
    )
    background_check_headline: str = "A new background"
    # The channel's graphics data (outro.json: the end card's fields and footer; fx.json, destinations.json,
    # georgia.json: the fact cards); Remotion reads the same files.
    graphics_dir: ChannelPath = Path("graphics")
    # The caption fonts in assets/fonts that the render's public dir gets (remotion/src/Captions.tsx loads them).
    fonts: tuple[str, ...] = ("Fredoka-Variable.ttf",)
    # Known entity names and their aliases for image search (JSON, name -> aliases); none without the file.
    entity_aliases: ChannelPath = Path("entity_aliases.json")
    # Set when loaded: the channel directory (every relative path in the config is under it).
    dir: Path = Path(".")
    # Identifies us to every site, and to robots.txt rules for robots_agent.
    user_agent: str = "reelline/{version}"
    robots_agent: str = "reelline"
    # The language of the channel's scripts: a pack in reelline/language (month names, number words, fact stems).
    language: str = "en"
    # The IANA zone of the channel's local time (the generated crontab's TZ; the engine itself uses utc_offset_h).
    timezone: str = "UTC"
    # The channel's local time: UTC + this many hours, no DST (slots, schedules, date-only dates).
    utc_offset_h: int = 0
    # Publish slots, local time; a slot's chain runs on steps.run_daily's schedule.
    slots: tuple[str, ...] = ("20:00",)

    def shown_name(self) -> str:
        """What the channel is called to people: display_name, else name."""
        return self.display_name or self.name


# ---- review ----------------------------------------------------------------------------------


class Telegram(Section):
    """reelline/notify/telegram.py, reelline/review/telegram.py: the review chat (both adapters)."""

    api_base: str = "https://api.telegram.org"
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 40.0
    # Audio, video and photo uploads (the review videos: up to 50 MB on a slow uplink).
    upload_write_timeout_s: float = 600.0
    upload_read_timeout_s: float = 180.0
    poll_timeout_s: int = 25
    text_max_chars: int = 4096
    header: dict[str, str | int] = {
        "rule": "━" * 20,
        "review": "🎬 Reel #{reel_id} · {date} {slot} · {title}",
        "menu": "🗳 Pick · {date} {slot}",
        "reel_prefix": "Reel #{reel_id} · ",
        "title_chars": 48,
    }
    bot_token_env: str = env("TELEGRAM_BOT_TOKEN", "Bot token from @BotFather (Telegram).")
    chat_id_env: str = env("TELEGRAM_CHAT_ID", "The review chat's ID (your own chat with the bot).", secret=False)


class Approve(Section):
    """reelline/pipeline/approve_bot.py, review.py."""

    reminder_before_slot_min: int = 60
    expire_after_h: int = 24
    expire_after_slot_h: int = 21
    caption_edit_timeout_min: int = 15
    max_regenerations: int = 3
    poll_timeout_s: int = 20
    error_backoff_s: int = 10
    job_error_chars: int = 3500
    labels: dict[str, str] = {
        "approve": "✅ Approve",
        "caption": "✏️ Caption",
        "regenerate": "🔁 Regenerate",
        "reject": "❌ Reject",
        "new_script": "New script",
        "new_voice": "New voice",
        "new_images": "New images",
        "other_story": "Other story",
        "back": "Back",
        "yes": "Yes",
        "no": "No",
        "post_now": "Post now",
        "next_slot": "Next free slot",
        "skip_slot": "Skip slot",
        "retry": "🔁 Retry",
        "post_now_ask": "🚀 Post now",
        "confirm": "🚀 Confirm",
        "cancel": "Cancel",
        "facts_wrong": "Facts wrong",
        "jokes_weak": "Jokes weak",
        "story_boring": "Story boring",
        "render_voice": "Render/voice issue",
        "other": "Other",
    }
    reject_story_reasons: tuple[str, ...] = ("facts_wrong", "story_boring")


class ReviewCli(Section):
    """reelline/review/cli.py: reviews printed; decisions from stdin or `reelline review tap|say`."""

    # `reelline review` drops each decision here as a file; the bot takes them in order.
    inbox_dir: ChannelPath = Path("out/review_inbox")
    stdin: bool = True  # also read lines from stdin when it's a terminal


class ReviewWebhook(Section):
    """reelline/review/webhook.py: every message POSTed as JSON to url; decisions polled from poll_url."""

    url: str = ""
    poll_url: str = ""
    timeout_s: float = 20.0
    token_env: str = env(
        "REVIEW_WEBHOOK_TOKEN", "Bearer token sent to the review webhook (review.webhook).", optional=True
    )


class ReviewAuto(Section):
    """reelline/review/auto.py: unattended channels: each review approved delay_s after it is sent."""

    delay_s: float = 0.0
    # The actions pressed, whichever the message offers first (QA failed: Yes; the slot passed: Post now).
    press: tuple[str, ...] = ("approve", "qa_yes", "post_now")


class Review(Section):
    # The review channel: telegram | cli | webhook | auto (reelline/review/registry.py).
    channel: Literal["telegram", "cli", "webhook", "auto"] = "telegram"
    telegram: Telegram = Telegram()
    cli: ReviewCli = ReviewCli()
    webhook: ReviewWebhook = ReviewWebhook()
    auto: ReviewAuto = ReviewAuto()
    approve: Approve = Approve()


# ---- notify ----------------------------------------------------------------------------------


class NotifySlack(Section):
    """reelline/notify/slack.py: an incoming webhook."""

    webhook_url_env: str = env(
        "SLACK_WEBHOOK_URL", "Slack incoming webhook URL (api.slack.com/messaging/webhooks).", optional=True
    )
    timeout_s: float = 20.0
    text_max_chars: int = 3900


class NotifyLog(Section):
    """reelline/notify/log.py: empty path: stdout."""

    path: str = ""


class Notify(Section):
    # Alerts, failures (Retry), stats, cost reports: telegram (the review chat) | slack | log.
    channel: Literal["telegram", "slack", "log"] = "telegram"
    slack: NotifySlack = NotifySlack()
    log: NotifyLog = NotifyLog()
