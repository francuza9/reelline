# Third-party components

reelline is Apache-2.0 ([LICENSE](LICENSE)). It uses, downloads or ships the components below, each under its own licence. This list covers what is in `engine/`, `channels/example/`, the `Dockerfile` and the dependency files. Licences were read from the installed package metadata or licence files on the date of writing; check a package's own repository before relying on a row. **Items marked ⚠ need your attention.**

## Shipped in the repository

| Component | Where | Licence | Notes |
|---|---|---|---|
| Fredoka (variable font) | `channels/example/assets/fonts/Fredoka-Variable.ttf` | SIL Open Font License 1.1 | Copyright 2016 The Fredoka Project Authors. The licence text travels with it: `OFL-Fredoka.txt`. Source and checksum: `engine/tools/README.md`. |
| Placeholder host | `engine/remotion/src/channel_default/remotion/Mascot.tsx`, `channels/example/assets/anchors.json` | this project (Apache-2.0) | flat SVG shapes drawn for this project; no image files |
| Example background | `channels/example/assets/backgrounds/studio.png` | this project (Apache-2.0) | a 1080x1920 picture of flat rectangles, an ellipse and a gradient (a wall, a screen, a window, a shelf, a plant) drawn for the example channel; no third-party artwork (checked by looking at it in the export audit) |
| Test fixtures | `engine/tests/golden/`, `engine/tests/fixtures/ingest/feed.xml` | this project (Apache-2.0) | invented feeds and an invented article (a fictional news item); no text copied from a real page |
| Example prompts, `sources.yaml`, `voice/voice.md` | `channels/example/` | this project (Apache-2.0) | written for this project |

No other fonts, images, audio or video are shipped in `engine/` or `channels/example/`. The music catalog of the example channel is empty. The release export (`ops/export/export.py`) is built from an allowlist and ships no binary other than the two files above; the README demo gif and every real-news fixture of the private repository are left out of it.

## Downloaded at build or run time

| Component | Used for | Licence | Notes |
|---|---|---|---|
| Rhubarb Lip Sync 1.14.0 | mouth shapes from audio | MIT (its own licence file says its bundled dependencies are MIT, BSD or similar) | not stored in git; downloaded by the Dockerfile and by the steps in `engine/tools/README.md`, with a SHA-256 check |
| Remotion's headless browser (Chrome for Testing) | rendering | Chromium's licences (BSD-style, with third-party components) | fetched by `npx remotion browser ensure` |
| MMS forced-aligner weights (torchaudio `MMS_FA`) | caption and lip-sync timing (Georgian only) | ⚠ licence of the weights not verified here; Meta's MMS models have been released under CC BY-NC 4.0 (non-commercial) | torchaudio downloads them on first use. **Check the terms before commercial use**; a channel that does not use the aligner never loads them |
| ffmpeg | audio and video processing | LGPL or GPL, depending on the build | installed from the system package manager (the Docker image uses Debian's); not redistributed by this repository |
| Debian base image, DejaVu, Liberation, Noto and Noto Color Emoji fonts | the Docker image | Debian package licences; the fonts are free licences (OFL, Bitstream Vera-derived) | installed by `apt` in the image only |
| Node 24 | the image's Node | MIT and others | copied from the official `node` image |

## Python dependencies (`engine/requirements.txt`, `engine/pyproject.toml`)

| Package | Licence |
|---|---|
| anthropic (optional extra) | MIT |
| google-genai (optional extra) | Apache-2.0 |
| python-dotenv | BSD-3-Clause |
| torch, torchaudio (CPU builds) | BSD-3-Clause |
| uroman | Apache-2.0 per its package classifier ⚠ (the metadata's licence field is the copyright notice; confirm in its repository) |
| numpy | BSD-3-Clause (and 0BSD, MIT, Zlib, CC0-1.0 parts) |
| httpx | BSD-3-Clause |
| pillow | MIT-CMU (HPND) |
| PyYAML | MIT |
| lxml | BSD-3-Clause |
| cssselect | BSD-3-Clause |
| requests | Apache-2.0 |
| feedparser | BSD-2-Clause |
| trafilatura | Apache-2.0 |
| Protego | BSD-3-Clause |
| boto3 | Apache-2.0 |
| pydantic | MIT |
| pytest and the dev tools (`engine/requirements-dev.txt`: ruff, mypy, ...) | development only; MIT or similar |

Their own dependencies are not listed; `pip-licenses` (or similar) on an installed environment lists them all. NLTK data (a local `cmudict` download under `engine/tools/nltk_data/`, gitignored) is not used by the code and is not shipped.

## npm dependencies (`engine/remotion/package.json`)

| Package | Licence |
|---|---|
| remotion, @remotion/cli | ⚠ the Remotion licence (not an OSI licence): free for individuals, for-profit organisations of up to 3 employees, non-profits and evaluation; **a company licence is required for larger for-profit organisations**. Read `node_modules/remotion/LICENSE.md` and <https://www.remotion.dev/license> and decide for your own use. This repository does not grant you Remotion's licence. |
| mediabunny | MPL-2.0 (file-level copyleft; unmodified use in a dependency tree is the normal case) |
| react, react-dom | MIT |
| typescript, @types/react (dev) | Apache-2.0; MIT |

The transitive npm dependencies are pinned in `package-lock.json` and carry their own (mostly permissive) licences; they are not listed here.

## Content the example channel fetches

The example channel's `sources.yaml` points at two US federal agencies' public news feeds. Their pages are generally US government works, in the public domain unless a page says otherwise or credits a third party. The feed URLs were not re-checked from this repository; check each source's terms and `robots.txt` before running (the fetcher honours `robots.txt`). Anything you ingest and publish is your responsibility: reelline does not make a source's content free to use.

## APIs

Model, speech, image and platform APIs the adapters call (and the keys they need) are governed by the terms of those services, not by this repository's licence.
