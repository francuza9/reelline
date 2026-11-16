"""Tunables for sound effects. Every number the SFX library and the SFX events use
lives here (the mix's own numbers stay in reelline/media/music.py)."""

from __future__ import annotations

from reelline.config import conf

# Tracked in git (unlike data/image_library/): the files are small and the server
# needs them. sfx.json is the license record, one entry per file.
SFX_DIR = conf().channel.dir / "assets" / "sfx"
CATALOG_NAME = "sfx.json"

# The license allowlist: exactly these names, nothing else. A license mapped to a
# tuple of hosts only comes from those sites (the source URL's host must be one of
# them or a subdomain); None means any site.
LICENSES: dict[str, tuple[str, ...] | None] = {
    "Pixabay Content License": ("pixabay.com",),
    "CC0": None,
}

# Stored format: 48 kHz stereo 16-bit WAV.
RATE = 48000
CHANNELS = 2

# `add` trims leading and trailing silence below this sample level.
TRIM_THRESHOLD_DB = -50.0
# Less than this left after the trim counts as a silent file.
MIN_DURATION_S = 0.01

# Per category:
#   max_duration_s  longer sounds (after the trim) are refused
#   normalize       "peak": sample peak to target_db dBFS (for clicks, hits and
#                   other short sounds); "lufs": integrated loudness to target_db
#                   LUFS (needs at least 0.4 s of sound after the trim)
#   gain_db         level in the mix relative to the voice, in the same measure:
#                   a "peak" sound's peak sits gain_db under the voice's sample peak,
#                   a "lufs" sound's loudness gain_db under the voice's. The render's
#                   --sfx-gain is added on top. Tune by ear.
CATEGORIES = {
    "tv_click": {"max_duration_s": 0.6, "normalize": "peak", "target_db": -1.0, "gain_db": -18.0},
    # The TV turning on / off (TV_POWER events).
    "tv_power": {"max_duration_s": 0.6, "normalize": "peak", "target_db": -1.0, "gain_db": -16.0},
    # max_duration_s: the sting plays after the voice under the end card and must fit in
    # its hold (remotion/src/outro.json holdS, 1.5 s); reelline/media/sfx/events.py
    # check_outro_fit() checks it (tests, and every render). 1.2 since 2026-10-25 (was 1.5,
    # for the 5 s card after the voice); the library's stings are 0.35-0.54 s.
    "sting": {
        "max_duration_s": 1.2,
        "normalize": "peak",
        "target_db": -1.0,
        "gain_db": -10.0,
        "max_note": "the sting must fit in the end card's hold after the voice (check_outro_fit)",
    },
    # The cold open (reelline/media/gags.py, channel docs/gags.md): each gag's action sound, cut to the action beat
    # (remotion/src/gags.json timing.caughtS, 1 s), and the one record scratch on the caught pose,
    # which ends by the cut (cutS - caughtS, 0.5 s; reelline/media/gags.py checks both). The gag has no voice,
    # so their gain_db is against the reel's own mix target (reelline/media/music.py MIX_LOUDNORM: I for a
    # lufs sound, TP for a peak one), and the action sound is measured by loudness (1 s of it).
    "gag": {
        "max_duration_s": 1.0,
        "normalize": "lufs",
        "target_db": -26.0,
        "gain_db": -1.0,
        "max_note": "it plays over the cold open's action beat (gags.json timing.caughtS)",
    },
    "scratch": {
        "max_duration_s": 0.5,
        "normalize": "peak",
        "target_db": -1.0,
        "gain_db": -4.0,
        "max_note": "it plays from the caught pose to the cut (gags.json timing)",
    },
}

# After normalization no stored sound may peak above this (dBFS); a loudness target
# that would push a sound past it fails instead of clipping.
MAX_STORED_PEAK_DB = -0.5

# ---- Events (reelline/media/sfx/events.py) ---------------------------------------------------
TV_CLICK = {
    # The click starts this long before the wall TV's visual switch (the first frame
    # of the change's fade), so its transient lands on the switch.
    "pre_offset_s": 0.06,
    # A change less than this after the last clicked change gets no click.
    "min_gap_s": 0.5,
    # A click that would overlap speech plays this much quieter (dB, on top of its
    # gain) instead of being skipped.
    "under_speech_db": -8.0,
    # Changes before this are the TV's opening state (frame 0, or a first image a few
    # frames in), not a switch: no click.
    "opening_s": 0.25,
}
# A fact graphic's card leaving the TV (remotion/src/Fx.tsx FxPopout, channel docs/fx.md): a TV click
# offset_s after the card's start (as it springs out), none as it flies back. It follows the
# click rules: none within TV_CLICK min_gap_s of another click, lowered under speech.
FX_POP = {
    "on": True,
    "offset_s": 0.03,
    "under_speech_db": -8.0,
}
TV_POWER = {
    # "tv_on" at the first moment the TV shows an image, instead of that change's
    # click; inside the opening window (TV_CLICK opening_s) it plays at the window's end.
    "on": True,
    # "tv_off" at the outro start, on the frame the screen starts going idle, before the sting.
    "off": True,
    # As for clicks: the sound starts this long before the switch's first drawn frame,
    # and plays this much quieter when it would overlap speech.
    "pre_offset_s": 0.06,
    "under_speech_db": -8.0,
}
STING = {
    # Seconds after the outro hook point (the outro's first frame, where
    # remotion/src/Reel.tsx starts its Sequence). Negative plays it earlier. A sting
    # that would overlap the voice under the end card (its start to the tag line's end)
    # or the "tv_off" sound moves to its end (plus gap_after_off_s after tv_off).
    "offset_s": 0.0,
    "gap_after_off_s": 0.05,
}

# ---- Mix (reelline/media/music.py mix(), sfx=...) ---------------------------------------------
# No SFX event peaks above the voice's own sample peak plus this (dB) in the mix;
# a louder event is turned down to it (and reported as capped). With the voice
# setting the mix's peaks, the final loudnorm (MIX_LOUDNORM) stays linear and the
# SFX can't push the mix into clipping.
PEAK_CEILING_DB = -3.0
# The final mix's loudnorm gain is measured without the SFX (voice + music, as
# before SFX), so they can't change it or flip it to dynamic mode. A mix with SFX
# whose true peak still ends up above this (dBTP) fails; turn them down with --sfx-gain.
MIX_TP_LIMIT_DB = -1.0

# ---- Credits (reelline/media/sfx/credits.py) --------------------------------------------------
# Neither license requires attribution, but every SFX a reel uses gets a line in its
# attributions.txt, in a section of its own after the image lines, so a caption can
# credit them if we want.
CREDITS = {
    "header": "# SFX",
    "line": conf().publish.credit_lines["sfx"],
    "author_max_chars": 40,
    # Site names by source URL host (or a parent domain); others show the host.
    "sites": {"pixabay.com": "Pixabay", "freesound.org": "Freesound"},
    # Include the SFX lines in caption output (scripts/send_to_phone.py). Off by default.
    "in_captions": False,
}

# ---- Freesound candidates (reelline/media/sfx/freesound.py, scripts/sfx.py fetch / pick) -----
# CC0 only; Pixabay has no sound-effects API, so its sounds stay manual (sfx.py add).
CANDIDATES_DIR = conf().channel.dir / "assets_src" / "sfx" / "candidates"
REJECTED_NAME = "rejected.json"

# Search presets per category. max_duration_s is the search's duration filter (it
# can be stricter than the library's CATEGORIES max_duration_s).
FETCH = {
    "tv_click": {
        "queries": (
            "tv channel switch",
            "tv click",
            "remote control click",
            "crt tv on",
            "static",
            "tv static",
            "switch",
            "click",
            "crt",
            "button click",
            "channel",
        ),
        "max_duration_s": 0.5,
    },
    "sting": {
        "queries": ("808 hit", "bass hit", "trap sting", "short transition hit", "lofi stinger"),
        "max_duration_s": 1.2,
    },
}

# freesound.org/docs/api (checked 2026-09-30): GET /apiv2/search/ (replaces the
# deprecated search/text endpoint), token auth ("Authorization: Token <key>"),
# Solr filters, previews in the sound record (no OAuth2 needed, unlike originals).
# Standard rate limit: 60 requests per minute, 2000 per day.
FREESOUND = {
    "api": "https://freesound.org/apiv2/search/",
    # One sound's record (the cold open's sounds are added by id: tools/gags/sfx.py).
    "sound_api": "https://freesound.org/apiv2/sounds/{id}/",
    # The filter value and the license field's value for CC0 in the API docs.
    "license_filter": "Creative Commons 0",
    # Most downloaded first ("rating_desc" sorts by average rating instead).
    "sort": "downloads_desc",
    # Filters on popularity; 0 turns one off.
    "min_downloads": 0,
    "min_avg_rating": 0,
    # Results fetched per query; candidates per run (spread over the queries in turn).
    "results_per_query": 15,
    "candidates_per_run": 8,
    # ~128 kbps MP3; "preview-lq-mp3", "preview-hq-ogg" and "preview-lq-ogg" also exist.
    "preview": "preview-hq-mp3",
    "fields": "id,name,username,license,duration,url,previews,num_downloads,avg_rating",
    "timeout_s": 30.0,
    "min_interval_s": 1.0,
    "retries": 2,
    "retry_wait_s": 10.0,
    # Candidate captions in Telegram.
    "name_max_chars": 60,
}
