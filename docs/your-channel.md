# Your own channel

From an empty folder to a first reel made from your own website. The first part is the minimum; everything after it is optional and can come later.

## The minimum

1. **Install.** The Docker image has everything (Python, Node, ffmpeg, Rhubarb): `CHANNEL_DIR=./my-channel docker compose up --build`. Without Docker, follow the [quick start](../README.md#quick-start).
2. **Create the channel.** `reelline init my-channel` copies `channels/example/`: a placeholder host drawn in SVG, a studio background, a font, an automatic review and a "publish" that only writes files to disk. You need no drawings to get a first reel.
3. **Keys.** `cp my-channel/.env.example my-channel/.env` and fill in the model keys and a voice id (`voice/voice.md`). The voice and the image generator use the `google` adapter today ([providers](providers.md)).
4. **Your source.** In `sources.yaml`, replace the example feeds with your site:
   - it has an RSS feed: `type: rss`, `url:` the feed;
   - it doesn't: `type: listing`, `url:` the page that lists the articles, `selector:` a CSS selector matching the links to each article.
   Set `lang:` to the site's language. Check the site's terms and `robots.txt` first.
5. **The channel block** in `config.yaml`: `name`, `display_name`, `contact`, `timezone`, `slots` (when a reel is due).
6. **Language.** Only Georgian (`channel.language: ka`) gets a fully voiced, lip-synced and captioned video today, because the forced aligner exists only for Georgian ([languages](languages.md)). Other languages get everything up to the script, the checks, the cover and stills. For a Georgian channel:
   - rewrite the prompts in `prompts/` in Georgian (the example's are English);
   - put a font with Georgian letters in `assets/fonts/` (the example's Fredoka has none) and name it in `channel.fonts` and `graphics/captions.json`;
   - consider turning the proofread pass back on (`steps.proofread.enabled: true`).
7. **Fit the prompts to your content.** The `assess` and `brief` prompts score and summarise stories, and the example's expect news and announcements (dates, who it is for, deadlines). For other content (recipes, a product catalogue, texts) say in them what makes a good story and which facts matter.
8. **Run it.**
   ```sh
   reelline doctor --channel my-channel            # config, chain, paths, keys
   reelline run --channel my-channel --dry-run     # the whole chain for the next slot, nothing sent
   reelline run --channel my-channel               # the reel lands in the local publish folder
   ```

## Optional

- **Your own host.** Set `kind: images` in `assets/rig.yaml` and add the drawings: the host (`host.png`, plus e.g. `chair.png`), a `mouths/` folder with one PNG per mouth shape (`A`–`H`, `X`, and `X_<mood>` for resting mouths per mood), optionally `poses/` and `angles/` for other camera angles. `assets/anchors.json` places the eyes and mouth and sets the colors. Or draw the host in code by replacing `remotion/Mascot.tsx` ([rendering](rendering.md#the-host-rig)).
- **Backgrounds.** More rooms in `assets/backgrounds/`, each PNG with a small JSON sidecar.
- **Music.** Tracks listed in `assets/music/music.json` (empty means no music).
- **Look and motion.** The end card (`graphics/outro.json`), fact graphics (`graphics/fx.json`), captions (`graphics/captions.json`), camera motion and angles (`graphics/motion.json`, `angles.json`).
- **Style.** Jokes on or off, cold opens, the host's mood, how many fact cards ([configuration](configuration.md)).
- **Human review.** Replace the automatic review with Telegram, a webhook or the CLI ([review and notifications](review-and-notify.md)).
- **Publishing.** YouTube, TikTok, Instagram/Facebook or Buffer instead of the local folder, plus stats and the weekly ranking ([publishing](publishing.md)).
- **On a timer.** systemd or cron so it runs every day by itself ([deployment](deployment.md)).

## Licences to check

The forced aligner's weights (Meta's MMS, downloaded on first use) have been released under a non-commercial licence, and Remotion has its own terms for companies. Read [THIRD_PARTY.md](../THIRD_PARTY.md) before using a channel commercially.
