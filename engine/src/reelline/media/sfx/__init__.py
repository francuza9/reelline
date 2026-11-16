"""Sound effects: a small licensed library and the per-reel SFX events.

- config.py     every tunable (license allowlist, categories, event rules, mix safety)
- library.py    assets/sfx/ (files + sfx.json license records), add / remove / checks
- events.py     per-reel events: TV clicks, the outro sting, seeded variant picks
- credits.py    the SFX section of attributions.txt
- freesound.py  CC0 candidates from Freesound: search, previews, Telegram, pick

The mixing is in reelline/media/music.py (mix(), sfx=...).

See channel docs/sfx.md.
"""
