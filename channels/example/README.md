# The example channel

The smallest English channel that runs the whole chain: two public US-agency feeds (NASA, NOAA), plain
informative prompts, a placeholder host drawn from `assets/rig.yaml`, an automatic review and a local
"publish" folder. It is the template `reelline init <dir>` copies.

```
reelline init ../my-channel --name my-channel     # a new channel from this one
reelline doctor --channel ../my-channel           # validate it
reelline run --channel ../my-channel --dry-run    # the chain for the next slot, nothing sent
```

What to change first: `sources.yaml` (the feeds), `prompts/` (the voice of the channel), `config.yaml`
(`channel`, the models, the slot), `assets/` (the host in `rig.yaml`, a background, a font) and `.env` (copy
`.env.example`: the model keys and the voice id).

Known limits (docs/split/PHASE6.md): a full video render needs lip sync and caption timing, which only the
Georgian language pack has; the assess and brief prompts still fill the engine's opportunity-shaped fields.
