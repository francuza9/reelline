"""The cost report's labels and groupings, and the render settings."""

from __future__ import annotations

from pathlib import Path

from .base import ChannelPath, Section, env

# ---- costs -----------------------------------------------------------------------------------


class Costs(Section):
    """reelline/pipeline/costs.py; /costs in the approval bot. A purpose pattern ending in "*" is a prefix."""

    steps: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("pick", "Pick / brief", ("assess", "brief")),
        ("story", "Story", ("story*",)),
        ("punchup", "Punch-up", ("punchup*",)),
        ("proofread", "Proofread", ("proofread*",)),
        ("factcheck", "Fact-check", ("factcheck*",)),
        ("endcard", "End card", ("endcard*",)),
        ("caption", "Caption", ("caption*",)),
        ("tts", "TTS (incl. re-rolls)", ("tts", "tts_reroll")),
        ("images", "Screen images (vision)", ("image*",)),
        ("qa", "QA vision", ("qa_vision",)),
        ("regenerations", "Regenerations", ()),
        ("other", "Other", ()),
        ("manual", "Other (manual)", ()),
    )
    regeneration_jobs: tuple[str, ...] = ("new_script", "new_voice", "new_images", "other_story")
    providers: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("anthropic", "Anthropic", ("claude",)),
        ("google", "Google", ("gemini",)),
    )
    other_provider: str = "Other"
    google_kinds: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("TTS", ("tts*",)),
        ("vision", ("image_vision", "image_relevance", "qa_vision")),
        ("image generation", ("image_generate*",)),
    )
    google_text: str = "Gemini text"
    overhead_purposes: tuple[str, ...] = ("assess",)
    reel_statuses: tuple[tuple[str, str], ...] = (
        ("approved", "approved"),
        ("published", "published"),
        ("rejected", "rejected"),
        ("superseded", "superseded"),
        ("expired", "expired"),
        ("render_failed", "failed"),
        ("rendered", "in review"),
    )
    reel_outcomes: dict[str, str] = {
        "approved": "delivered",
        "published": "delivered",
        "rendered": "pending",
        "rejected": "waste",
        "superseded": "waste",
        "expired": "waste",
        "render_failed": "waste",
    }
    outcomes: tuple[tuple[str, str], ...] = (
        ("delivered", "Approved / published"),
        ("waste", "Went nowhere"),
        ("pending", "In review / in progress"),
        ("overhead", "Overhead (assess)"),
        ("manual", "Manual (dev)"),
        ("unattributed", "Unlinked"),
    )
    no_reel: dict[str, str] = {
        "pick": "pick skipped or dropped",
        "script": "script failed",
        "job": "failed job",
        "replaced": "script replaced",
        "pending": "in progress",
    }
    reel_parts: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("images", ("image*",)),
        ("QA", ("qa_vision",)),
        ("TTS", ("tts*",)),
    )
    reel_parts_rest: str = "LLM"
    top_n: int = 5
    title_chars: int = 32
    months_back: int = 12
    buttons: dict[str, str] = {"this": "This month", "last": "Last month", "pick": "Pick month", "csv": "📄 CSV"}
    daily_bar_chars: int = 12
    footer: str = "Tracked API spend only. Fixed costs: $0."
    untracked: tuple[str, ...] = ()
    csv_columns: tuple[str, ...] = (
        "timestamp",
        "timestamp_tbilisi",
        "provider",
        "model",
        "step",
        "purpose",
        "pick",
        "slot",
        "reel",
        "script",
        "job",
        "outcome",
        "manual",
        "estimated",
        "tokens_in",
        "tokens_out",
        "tokens_thinking",
        "usd",
    )


# ---- render ----------------------------------------------------------------------------------


class Pixabay(Section):
    api_key_env: str = env(
        "PIXABAY_API_KEY",
        "Pixabay API key (pixabay.com/api/docs): stock photos for generic scenes; "
        "without it, the image library and Commons only.",
        optional=True,
    )


class Pexels(Section):
    api_key_env: str = env("PEXELS_API_KEY", "Pexels API key (pexels.com/api); skipped when empty.", optional=True)


class Freesound(Section):
    api_key_env: str = env(
        "FREESOUND_API_KEY", "Freesound API key (freesound.org/apiv2/apply); scripts/sfx.py fetch only.", optional=True
    )


class Render(Section):
    # The reel opening's version, stamped on every new reel (reels.opening_version) for /analytics.
    opening_version: int = 1
    # Remotion's parallel frames (engine/remotion/remotion.config.ts); None: Remotion's default.
    remotion_concurrency: int | None = None
    # The renderer (reelline/render: remotion) and the host: a folder with rig.yaml (drawings, mouth
    # shapes, poses, angles; reelline/render/base.py HostRig).
    renderer: str = "remotion"
    rig_dir: ChannelPath = Path("assets")
    pixabay: Pixabay = Pixabay()
    pexels: Pexels = Pexels()
    freesound: Freesound = Freesound()
