# reelline

[![CI](https://github.com/francuza9/reelline/actions/workflows/ci.yml/badge.svg)](https://github.com/francuza9/reelline/actions/workflows/ci.yml)

**A pipeline that turns news into short vertical videos: it finds the stories, writes and fact-checks the script, voices and renders the reel, asks a human to approve it, and publishes it.**

**[Watch the 45-second demo](https://tskdev.org/projects.html)**: the whole chain running on the channel it was built for.

*The engine was built for a Georgian-language channel that ran as an experiment; the English example channel in `channels/example/` is the generic starting point.*

## What it is built for

1. **Cheap.** Every model call is recorded with its tokens and price in a ledger; each job (writing, checking, vision, voice) is a separate role, so a cheaper model can take the jobs that don't need a better one ([costs](docs/costs.md)).
2. **Good.** Every claim in a script is checked against the source before anything is rendered, and a failing sentence goes back to the step that wrote it ([fact-checking](docs/fact-checking.md)). A human approves each reel.
3. **Automatic.** A timer runs one command; the chain resumes where it stopped after a failure, and the only manual step is a tap on *Approve* ([deployment](docs/deployment.md)).

## Quick start

You need Python 3.12 or newer. A full render also needs Node (for Remotion), ffmpeg and [Rhubarb](engine/tools/README.md); the Docker image has all of them.

```sh
git clone <this repository's URL> reelline && cd reelline
python -m venv .venv && . .venv/bin/activate
pip install -e "engine[anthropic,google]"        # the extras are the model providers you use ([all] for every adapter)
(cd engine/remotion && npm ci)                    # the renderer's packages

# or, without installing anything locally:
#   CHANNEL_DIR=./my-channel docker compose up --build

reelline init my-channel                           # a new channel, copied from channels/example
cp my-channel/.env.example my-channel/.env        # add your model keys; credentials live only here
reelline doctor --channel my-channel               # validates the config, the chain, the paths, the credentials
reelline run --channel my-channel --dry-run        # the chain for the next slot; nothing is sent or published
```

`reelline init` also writes `.env.example`, so a fresh channel lists exactly the credentials its config needs. [docs/deployment.md](docs/deployment.md) has the Docker, systemd and cron setups.

## The chain

The daily run is a list of steps in the channel's `config.yaml` (`pipeline:`). Each step declares the artifacts it needs and makes, and the loader refuses a chain where a step's input is not made by an earlier step. The default order:

```mermaid
flowchart LR
  ingest --> assess --> pick --> brief --> write --> style --> proofread --> factcheck
  factcheck -- "failing sentence: rerun the step that wrote it" --> write
  factcheck --> cards --> fx --> caption --> save --> render --> qa --> review --> publish --> stats --> rank
  rank -. "what worked, shown to write" .-> write
```

`voice` is a further step for library use (speech from text). Each step is described in [docs/steps.md](docs/steps.md); the picture of how they fit is in [docs/architecture.md](docs/architecture.md).

## Use only what you need

A step runs on plain inputs, with no database, so each part works alone.

- **Fact-check a script you wrote.** `reelline step factcheck --input inputs.json` takes a JSON file with the source text (`"Story"`), the facts sheet (`"Brief"`), the plain first draft (`"Draft"`) and the script (`"Script"`; give your text as both when there is no separate draft) and prints the claims with verdicts, evidence quotes and notes. ([fact-checking](docs/fact-checking.md))
- **A news site as a filtered digest.** Put the site in `sources.yaml` and set `pipeline: [ingest, assess]` (add `pick` and `brief` for a facts sheet per story). `reelline run` fetches new stories and scores them with your assess prompt; nothing is written or rendered. ([steps](docs/steps.md))
- **Render with your own host.** Give `render.rig_dir` a folder with a `rig.yaml` and your drawings (or the built-in `kind: svg` placeholder host) and your own `Mascot.tsx`. ([rendering](docs/rendering.md))
- **An English channel without proofread.** Set `steps.proofread.enabled: false` (the example channel already does). The English language pack is minimal and the forced aligner behind lip sync and caption timing exists for Georgian only, so for now an English channel gets everything up to the script, checks, cover and frame stills, not a fully voiced video. ([languages](docs/languages.md))

## Configuration

One schema (`engine/src/reelline/config/schema/`) holds every setting with a generic default; a channel's `config.yaml` sets only what differs, credentials live only in the channel's `.env`, and any setting can be overridden for one run with `--set section.key=value`. `reelline config show` prints the merged result. The full list of keys is in [docs/configuration.md](docs/configuration.md).

## Documentation

[Architecture](docs/architecture.md) · [Configuration](docs/configuration.md) · [Steps](docs/steps.md) · [Providers](docs/providers.md) · [Review and notifications](docs/review-and-notify.md) · [Publishing and stats](docs/publishing.md) · [Languages](docs/languages.md) · [Rendering](docs/rendering.md) · [Fact-checking](docs/fact-checking.md) · [Deployment](docs/deployment.md) · [Costs](docs/costs.md)

See also [CONTRIBUTING](CONTRIBUTING.md), [SECURITY](SECURITY.md), [CHANGELOG](CHANGELOG.md) and [THIRD_PARTY](THIRD_PARTY.md).

## Licence

Apache-2.0, see [LICENSE](LICENSE). Third-party components keep their own licences ([THIRD_PARTY.md](THIRD_PARTY.md)); note in particular that Remotion has its own licence terms for companies.
