"""Wall-TV images: licensed-source search, license records, vision check, library.

- config.py     every tunable (sources, candidate caps, vision, framing, API settings)
- query.py      query types (generic scene / named entity), exact entity matching
- licenses.py   Commons license allowlist and attribution lines
- sources.py    Pixabay, Wikimedia Commons, Pexels, generated fallback
- vision.py     Gemini Flash vision check, cached per (image, paragraph)
- framing.py    photo crop / focal point, logo panel color, min resolution
- library.py    data/image_library/ (files + library.sqlite)
- cooldown.py   images shown in recent reels (not picked again for a while)
- resolve.py    resolve_reel(): every slot, images.json and attributions.txt (re-exports the below)
- picker.py     Picker: per-paragraph candidate search, vision checks, pick
- model.py      errors, Evaluation, Pick
- manifest.py   images.json entries, attribution lines

See channel docs/images.md.
"""
