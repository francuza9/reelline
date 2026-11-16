# Changelog

## 0.1.0 (unreleased)

The first open-source release of the engine, split out of a Georgian-language channel that ran as an experiment.

### Pipeline
- A chain of 18 built-in steps configured in `pipeline:` (ingest, assess, pick, brief, write, style, proofread, factcheck, cards, fx, caption, save, render, qa, review, publish, stats, rank) plus a `voice` library step; each step declares what it needs and makes, and a broken chain is refused on load with a message naming the steps.
- Enable, disable or replace any step (`steps.<name>.enabled`, `steps.<name>.use`); add a channel's own steps from its `plugins/` folder.
- A runner that resumes from stored passes and runs a generic repair loop for checks.
- Steps usable on plain inputs without a database: `reelline step NAME --input FILE`, `reelline run --input FILE`.
- Fact-checking with a draft lock, claim verdicts with evidence quotes and a bounded repair loop.

### Providers and adapters
- Model providers by role: `anthropic`, `google` and `openai` (any OpenAI-compatible endpoint), with a shared retry policy and a cost ledger.
- Review channels: telegram, cli, webhook, auto. Notifiers: telegram, slack, log.
- Publishers: buffer, meta, youtube, tiktok, local. Stats collectors: buffer, meta, youtube, tiktok. Blob storage: s3-compatible, local.

### Channels and rendering
- One typed config schema with generic defaults, per-channel `config.yaml`, credentials only in `.env`, `--set` overrides, `reelline config show`, `reelline config docs` (generates `docs/configuration.md`) and `reelline doctor`.
- Language packs (`ka`, `en`).
- Remotion renderer reading a channel's graphics, fonts and host through `@channel/...`, with a generic placeholder host (`kind: svg`).
- `reelline init` creates a channel from `channels/example`, an English channel that runs the whole chain on fakes in its golden test.
- Dockerfile and compose file; systemd units as a reference deployment.

### Quality gates
- CI: tests, ruff, mypy (strict set), a leak guard that keeps channel-specific names and private details out of `engine/`, the Remotion type check and bundle, and the Docker build.

### Known limits
- Caption timing and lip sync need a forced aligner that exists only for Georgian; other languages get everything up to the script, checks and stills.
- The package name `reelline` is provisional.
