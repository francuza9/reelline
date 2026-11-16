# Rendering

Reels and stills are rendered by [Remotion](https://www.remotion.dev/) (React, headless Chrome) from the files the `save` step writes, with the voice and images prepared by Python first. The renderer sits behind a protocol (`reelline/render/base.py`); `render.renderer: remotion` is the one shipped.

## Pieces

- `engine/remotion/`: the project. Compositions: `Reel` (the video), `Cover` (the cover still), `EndCard` and `FxPreview` (stills of the end card and a fact graphic, used for checks and previews). The encoding settings are in `engine/remotion/encode.json`, shared with QA.
- The voice: the TTS role speaks each sentence; Rhubarb turns the audio into mouth shapes (visemes); a forced aligner times the captions (Georgian only today, see [languages](languages.md)).
- Images: selected per scene from the channel's image library, free stock and commons sources, with a vision check, and generated as a fallback ([providers](providers.md)).
- `render.remotion_concurrency` limits Remotion's parallel frames (unset: its default, half the CPUs). Lower it on a small machine ([deployment](deployment.md)).

## The channel's files, `@channel`

The engine's Remotion code never contains a channel's drawings or data. It imports `@channel/...`, which webpack resolves in the channel folder first (the renderer sets `REELLINE_CHANNEL_DIR` to it on every call) and, for a file the channel does not have, in `engine/remotion/src/channel_default/`. The project therefore builds and type-checks with no channel present. `@engine/...` is `src/`, for a channel component that imports engine modules.

| Channel file | What it is | Default |
|---|---|---|
| `graphics/outro.json` | the end card's fields and footer | yes (English) |
| `graphics/fx.json`, `destinations.json`, `georgia.json` | fact-graphic labels and data | yes |
| `graphics/captions.json` | caption fonts and style | yes |
| `graphics/motion.json`, `angles.json` | camera motion; the camera angles | yes (angles off) |
| `assets/anchors.json` | the host's colors and the placement of eyes and mouth | yes |
| `remotion/Mascot.tsx` | the React component that draws the host (`channel.mascot_component`) | the placeholder host |
| `assets/fonts/` | the fonts in `channel.fonts` | |
| `assets/backgrounds/` | room backgrounds, each with a small JSON sidecar (the idle TV's mark) | |
| `assets/music/music.json` | the music catalog; may be empty (no music) | |

## The host rig

The host is data: a folder named by `render.rig_dir` containing `rig.yaml` (`reelline/render/base.py`, `HostRig`):

```yaml
name: my-host
kind: images            # images (default): drawings and mouth shapes the renderer stages; svg: drawn from anchors.json
anchors: anchors.json   # colors, face-part placement, mouth by mood
drawings: [host.png, chair.png]   # staged at the public dir's root       (images only)
mouths: mouths          # dir of <code>.png, one per mouth shape A-H, X, X_<mood>  (images only)
poses: poses            # dir of <name>.png (optional)
angles: angles          # art for the other camera angles (optional)
```

### The placeholder host (`kind: svg`)

`channels/example/assets/rig.yaml` is `kind: svg`: nothing to stage, the renderer draws a simple host from `anchors.json` with `channel_default/remotion/Mascot.tsx`: a rounded body on a chair, round eyes with lids and pupils, nine mouth shapes (A-H, X) and three rests (neutral, happy, surprised), all flat SVG drawn for this project. Change `anchors.json` for the colors and placement, or replace `remotion/Mascot.tsx` in your channel folder with your own component to draw something else entirely; the rest of the pipeline does not care.

## Previewing

- `engine/remotion` has a studio: `npm run studio` (set `REELLINE_CHANNEL_DIR` to your channel folder to see its files).
- A still of the cover, with your host and background, is the quickest check of a rig: `REELLINE_CHANNEL_DIR=<channel> npx remotion still Cover out.png --public-dir=<dir with fonts/ and backgrounds/> --props=...` (the CI smoke test does exactly this for the example channel).
- The golden test (`engine/tests/test_golden_example.py`) renders the example channel's cover and one reel frame in a cheap mode and encodes a short video from the cover; a full render with voice needs the aligner.

## Requirements

Node (the CI uses 24), `npm ci` in `engine/remotion` (also downloads Remotion's browser: `npx remotion browser ensure`), ffmpeg, and Rhubarb in `engine/tools/rhubarb/` (the version and checksum are in `engine/tools/README.md`). The Docker image has all of them. Remotion has its own licence terms: see [THIRD_PARTY.md](../THIRD_PARTY.md).
