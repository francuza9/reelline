# Architecture

reelline is a library of steps, a runner that chains them, and adapters for everything outside the process (models, chat, platforms, storage, the renderer). A *channel* is a folder of configuration, prompts, sources and assets that the engine runs; the engine contains no channel's content.

## Layout

| Path | What is in it |
|---|---|
| `engine/src/reelline/` | the `reelline` Python package |
| `engine/remotion/` | the Remotion project that renders the reel and its stills |
| `engine/scripts/`, `engine/tools/` | dev and one-off scripts; third-party binaries (Rhubarb), recorded in `engine/tools/README.md` |
| `engine/tests/` | the unit tests and the golden test of the example channel |
| `channels/example/` | the generic English channel `reelline init` copies |
| `ops/` | systemd units and deploy helpers (one possible deployment, see [deployment](deployment.md)) |

Inside the package:

| Package | Role |
|---|---|
| `config/` | the schema (pydantic), the loader (defaults, then the channel's `config.yaml`, then `--set`), credentials from `.env` |
| `steps/` | one thin module per pipeline step, each a `Step` |
| `pipeline/` | the logic the steps wrap: ingest, assess, pick, script passes, fact-check, produce, publish, stats, costs, the approval bot, the daily tick, the SQLite state |
| `media/` | scripts as markup, TTS, lip-sync visemes, image selection, fx cards |
| `providers/` | model adapters and the cost ledger |
| `review/`, `notify/` | where reviews and alerts go, and how decisions come back |
| `publish/`, `stats/` | platform adapters |
| `storage/` | blob storage for the files platforms fetch |
| `render/` | the renderer protocol and the host rig |
| `language/` | language packs |

## The step contract

Defined in `reelline/pipeline/step.py`.

- A **step** has a `name`, the artifacts it `needs` (and optionally `wants`) and `makes`, and `run(ctx) -> StepResult`.
- An **artifact** is a named thing passed between steps: `Stories`, `Assessments`, `Story`, `Brief`, `Draft`, `Script`, `DraftLock`, `FactCheck`, `Cards`, `Graphics`, `Caption`, `ScriptFile`, `Audio`, `Video`, `QA`, `Review`, `Post`, `Stats`, `Ranking`. A channel's own step may use other names (plain strings).
- A **check** is a step that `judges` artifacts: its result lists rejected artifacts with reasons.
- A step may be marked `sends` (it talks to a person or a platform); it is skipped in a dry run.
- The **context** carries the artifacts so far, a result store, the providers by role, the database connection (none in library mode), the run's state and the failures a check reported.

## The chain

`pipeline:` in `config.yaml` lists the steps in order. On load (`reelline/pipeline/chain.py`) the chain is validated, and `reelline doctor` reports problems. The load fails, naming the step, when a step is unknown, a `use:` class cannot be imported or is not a `Step`, a step appears twice, or an enabled step needs an artifact that no enabled step before it makes.

Switches per step, under `steps.<name>`:

- `enabled: false` leaves the step out (steps after it that need its output then fail the load);
- `use: "module:Class"` runs another class in its place;
- a name under `steps:` that is not a built-in step is a channel's own step (its `use:` names the class; a module in the channel's `plugins/` folder is importable as `plugins.<name>`), with free settings of its own.

## The runner

`reelline/pipeline/runner.py` runs the steps in order and keeps each pass's result in a store, so a rerun goes on after the last finished pass instead of repeating (and paying for) earlier calls. Statuses: `ok`, `wait` (nothing more until someone acts, such as a review), `failed`, `skipped`.

The **repair loop** is generic: when a check rejects an artifact, the runner sends the work back to the first step of the run that makes that artifact without needing it, with the reasons in `ctx.failures`, reruns from there through the check, and does this at most `steps.<check>.repair_rounds` times. See [fact-checking](fact-checking.md).

## State

Runs are recorded in a SQLite database (`storage.db_path`): stories, assessments, picks, briefs, scripts and their passes, reels, reviews, publications, post statistics and the model-call ledger. Files (scripts, renders, logs) go under the channel's `content/` and `out/` folders; the paths are `storage.*` settings. The state is the channel's, never the engine's.

## The daily run

`reelline tick` is what a timer runs (every few minutes). On each tick it:

1. starts the chain for a slot that is due (`channel.slots`), as a set of jobs;
2. ingests sources on their own schedule;
3. supervises the approval bot, the long-running listener that handles review decisions and publishes after *Approve*;
4. collects post statistics and ranks what worked.

`reelline run` and `reelline step` are the manual entry points to the same chain (`--from`, `--to`, `--dry-run`, `--input`). Timeouts and retry limits of each job are `steps.run_daily.*`.

## Library use

The steps that make sense without a database also have a module-level `run()` on plain inputs: `write`, `style`, `proofread`, `factcheck`, `cards`, `fx`, `render`, `voice`. `reelline step NAME --input FILE` and `reelline run --input FILE [--store FILE]` run steps on JSON artifacts and print what was made.

## Extension points

Each is a small protocol with a registry; adding one is a class plus one branch in the registry:

| Point | Protocol | Adapters shipped | Setting |
|---|---|---|---|
| model provider | `providers/base.py` | anthropic, google, openai (any OpenAI-compatible endpoint) | `models.*` |
| review channel | `review/base.py` | telegram, cli, webhook, auto | `review.channel` |
| notifier | `notify/base.py` | telegram, slack, log | `notify.channel` |
| publisher | `publish/base.py` | buffer, meta, youtube, tiktok, local | `publish.targets` |
| stats collector | `stats/base.py` | buffer, meta, youtube, tiktok | `steps.analytics.collectors` |
| blob storage | `storage/blob.py` | s3 (S3-compatible), local | `storage.blob.kind` |
| renderer | `render/base.py` | remotion | `render.renderer` |
| language pack | `language/base.py` | ka, en | `channel.language` |
| step | `pipeline/step.py` | the steps in [steps](steps.md) | `pipeline`, `steps.<name>.use` |
