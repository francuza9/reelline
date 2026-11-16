"""Settings of the ingest, pick, brief, script, proofread, factcheck, endcard and caption steps."""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict

from .base import ChannelPath, Number, Section


class StepConf(Section):
    """The switches every pipeline step has (reelline/pipeline/chain.py): enabled false leaves it out
    of the chain (refused on load when a later step needs what it makes); use names another class,
    "package.module:Class" (a module under the channel's plugins/ too), meeting reelline/pipeline/step.py's
    contract, to run instead."""

    enabled: bool = True
    use: str | None = None


class FrameCheck(Section):
    """One yes/no question the frame check asks the vision model: its JSON key, the answer that fails the
    frame, and the problem's label in the QA report."""

    key: str
    fails_when: bool
    label: str


class Qa(StepConf):
    """reelline/pipeline/qa: the frame check's prompt and questions (the channel's scene and mascot)."""

    frame_prompt: ChannelPath = Path("prompts/qa_frame.md")
    frame_checks: tuple[FrameCheck, ...] = ()


class CustomStep(StepConf):
    """A step only the channel has (steps.<name> with its use: path); its own settings ride along."""

    model_config = ConfigDict(extra="allow", validate_default=True, revalidate_instances="always")
    repair_rounds: int = 1


class Ingest(StepConf):
    """reelline/pipeline/ingest.py, fetch.py, urls.py."""

    connect_timeout_s: float = 10.0
    read_timeout_s: float = 20.0
    retries: int = 2
    retry_wait_s: float = 3.0
    max_retry_after_s: float = 30.0
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    min_interval_s: float = 2.0
    max_crawl_delay_s: float = 60.0
    max_bytes: int = 5_000_000
    robots_max_bytes: int = 512_000
    first_run_days: int = 3
    first_run_keep_undated: bool = False
    min_words: int = 80
    max_items_per_source: int = 30
    max_failures: int = 5
    # A date without a time becomes this local time of day.
    date_only_time: str = "12:00"
    max_future_skew_h: int = 24
    permanent_statuses: tuple[int, ...] = (401, 403, 404, 410, 451)
    lang_min_share: float = 0.6
    report_days: int = 7
    # Query parameters dropped from every URL: these names, and any starting with a prefix.
    tracking_params: frozenset[str] = frozenset(
        {
            "fbclid",
            "gclid",
            "dclid",
            "gbraid",
            "wbraid",
            "msclkid",
            "yclid",
            "twclid",
            "igshid",
            "mc_cid",
            "mc_eid",
            "_hsenc",
            "_hsmi",
            "mkt_tok",
            "ref_src",
            "ref_url",
            "cmpid",
            "ocid",
            "guccounter",
            "guce_referrer",
            "guce_referrer_sig",
            "spm",
            "_ga",
            "_gl",
        }
    )
    tracking_prefixes: tuple[str, ...] = ("utm_",)


class Pick(StepConf):
    """reelline/pipeline/pick.py, assess.py, scoring.py, techpick.py."""

    assess_prompt: ChannelPath = Path("prompts/assess.md")
    assess_prompt_version: int = 1
    assess_batch: int = 8
    assess_max_chars: int = 3000
    # `assess --force --grep <name>`: named case-insensitive regexes over title and text.
    grep_presets: dict[str, str] = {}
    min_deadline_days: int = 2
    max_age_days: float = 7.0
    repeat_days: int = 30
    repeat_fuzzy: float = 0.85
    exam_every_days: int = 7
    score_weights: dict[str, float] = {"open_status": 0.4, "reach": 0.3, "provider_tier": 0.2, "novelty": 0.1}
    score_points: dict[str, dict[str, Number]] = {
        "open_status": {"open": 10, "upcoming": 8, "unclear": 3},
        "reach": {"broad": 10, "medium": 6, "narrow": 2},
        "provider_tier": {
            "government": 10,
            "international": 10,
            "university": 10,
            "major_company": 8,
            "established_academy": 7,
            "established_ngo": 7,
        },
        "novelty": {"new": 10, "ongoing": 5},
    }
    decay_per_day: float = 0.3
    deadline_bonus: float = 1.0
    deadline_bonus_days: tuple[int, int] = (5, 14)
    unclear_relevance_penalty: float = 2.0
    reassess_days: int = 30
    hook_weight: float = 0.3
    # The channel's topic (ranked on the opportunity rules) and the topic with one slot a week ("" for none),
    # as the assessment names them.
    topic: str = "education"
    weekly_topic: str = "tech"
    # The weekly slot (publish weekday, 0 = Monday) and its candidates.
    tech_weekday: int = 2
    tech_min_score: float = 7.0
    tech_max_assess_per_day: int = 8
    tech_max_age_days: float = 3.0
    tech_sources: tuple[str, ...] = ()
    tech_big_names: tuple[str, ...] = ()
    tech_dedupe_fuzzy: float = 0.6
    contender_min: float = 7.5
    contender_within: float = 1.0
    menu_size: int = 5
    menu_timeout_min: int = 90


class Brief(StepConf):
    """reelline/pipeline/brief.py."""

    prompt: ChannelPath = Path("prompts/brief.md")
    prompt_version: int = 1
    max_chars: int = 12000
    max_numbers: int = 5


class Script(Section):
    """reelline/pipeline/script.py: the story pass, punch-up (or straight), the flirt line."""

    story_prompt: ChannelPath = Path("prompts/story.md")
    punchup_prompt: ChannelPath = Path("prompts/punchup.md")
    straight_prompt: ChannelPath = Path("prompts/straight.md")
    flirt_on_prompt: ChannelPath = Path("prompts/flirt_on.md")
    flirt_off_prompt: ChannelPath = Path("prompts/flirt_off.md")
    flirt_banned_prompt: ChannelPath = Path("prompts/flirt_no_eye_stalks.md")
    # The share of reels with a flirt line (0: never), and its angles (one picked per roll).
    flirt_probability: float = 0.0
    flirt_angles: tuple[str, ...] = ()
    # A stem from flirt_banned_stems in one of the last flirt_banned_every - 1 flirt lines bans it from the next.
    flirt_banned_every: int = 10
    flirt_banned_stems: tuple[str, ...] = ()
    # The retry note when a flirt line uses a banned phrase too soon ({recent}: how many recent lines, {every}).
    flirt_banned_note: str = (
        "The flirt line uses a banned phrase, but one of the last {recent} flirt lines already did."
    )
    story_words: tuple[int, int] = (60, 110)
    story_min_georgian: float = 0.8
    # Every script ends with this paragraph, alone (media/script_markup); hyphens, never em dashes.
    tag_line: str = "Thanks for watching."
    # The story prompt's delivery phrase that straight style rewrites ([old, new]; the phrase must be in the
    # prompt exactly once). None: the prompt needs no rewrite for a straight style.
    straight_opening: tuple[str, str] | None = (
        "(outraged, disgusted or in mock excitement)",
        "(straight and upbeat, never sarcastic)",
    )
    opening_max_words: int = 12
    paragraph1_max_words: int = 18
    explanation_max_words: int = 15
    hook_examples_max: int = 5
    same_ratio: float = 0.97
    changed_ratio: float = 0.6
    lookahead: int = 4
    recent_days: int = 30
    recent_max: int = 40
    recent_statuses: tuple[str, ...] = ("ok", "approved", "posted")
    recent_flirts: int = 20
    recent_details: int = 10
    recent_shares: int = 10
    story_prompt_version: int = 1
    punchup_prompt_version: int = 1
    straight_prompt_version: int = 1
    opening_lookback: int = 10


class Proofread(StepConf):
    """reelline/pipeline/proofread.py."""

    prompt: ChannelPath = Path("prompts/proofread.md")
    prompt_version: int = 1
    word_tolerance: float = 0.10
    # {{KIND}}: what the text is.
    kinds: dict[str, str] = {
        "script": "reel script, in the script format described below",
        "hook": "post caption hook: 1-2 sentences of plain text, no markup",
    }


class Factcheck(StepConf):
    """reelline/pipeline/factcheck.py."""

    # Repair rounds after a failing check (reelline/pipeline/runner.py): the work it rejects goes
    # back to the step that made it (a draft failure: write, then style; a style one: style).
    repair_rounds: int = 1
    prompt: ChannelPath = Path("prompts/factcheck.md")
    prompt_version: int = 1
    max_source_chars: int = 12000


class Endcard(Section):
    """reelline/pipeline/endcard.py."""

    prompt: ChannelPath = Path("prompts/endcard.md")
    prompt_version: int = 1


class Caption(StepConf):
    """reelline/pipeline/caption.py; limits are checked by reelline/pipeline/qa.py."""

    prompt: ChannelPath = Path("prompts/caption.md")
    prompt_version: int = 1
    hook_sentences: tuple[int, int] = (1, 2)
    youtube_title_max: int = 60
    hashtags: tuple[int, int] = (3, 5)
    required_hashtags: tuple[str, ...] = ()
    max_emoji: int = 1
    source_line: str = "Source: {outlet} - {url}"
    ai_line: str = "Partly made with AI"
    limits: dict[str, dict[str, int]] = {
        "instagram": {"chars": 2200, "hashtags": 30},
        "tiktok": {"chars": 2200},
        "youtube_description": {"chars": 5000},
        "youtube_title": {"chars": 100},
        "facebook": {"chars": 2200},
    }
