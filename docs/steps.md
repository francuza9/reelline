# Steps

The chain is the list `pipeline:` in the channel's `config.yaml` (default: the 18 steps below, in this order; `voice` is a 19th built-in that is not in the default list). A step's *needs* must be made by an enabled step before it. `wants` are optional inputs used when present.

Any step can be switched off with `steps.<name>.enabled: false` or replaced with `steps.<name>.use: "module:Class"` (a subclass of `reelline.pipeline.step.Step`). `reelline doctor` and every run check the chain first and name the step and the missing artifact. Replacement classes must declare the same `needs`/`makes` for the steps around them to keep loading. See [architecture](architecture.md) for the contract.

| # | Step | Needs | Makes | Can be left out? |
|---|---|---|---|---|
| 1 | `ingest` | | Stories | yes, if stories come from elsewhere |
| 2 | `assess` | Stories | Assessments | yes: `pick` assesses what is left itself |
| 3 | `pick` | Assessments | Story | no, unless another step makes Story |
| 4 | `brief` | Story | Brief | no (write needs it) |
| 5 | `write` | Brief | Draft | no |
| 6 | `style` | Brief, Draft | Script | no |
| 7 | `proofread` | Script | Script | yes |
| 8 | `factcheck` | Story, Script, Brief, Draft | FactCheck, DraftLock | yes, but nothing then checks claims |
| 9 | `cards` | Story, Script, Brief | Cards | yes |
| 10 | `fx` | Story, Script, Brief | Graphics | yes |
| 11 | `caption` | Script | Caption | yes |
| 12 | `save` | Script (wants Cards, Graphics, Caption) | ScriptFile | no (render needs it) |
| 13 | `render` | ScriptFile | Video, Audio | no for a reel |
| 14 | `qa` | Video | QA | yes |
| 15 | `review` | Video (wants QA) | Review | yes, with `publish` too |
| 16 | `publish` | Review | Post | yes (render-only channels) |
| 17 | `stats` | Post | Stats | yes |
| 18 | `rank` | Stats | Ranking | yes |
| | `voice` | Script | Audio | library step |

Settings of steps that share a section are not steps of their own: `steps.script` (write and style), `steps.endcard` (cards), `steps.produce` (render, qa, review), `steps.run_daily` (the tick), `steps.retention` (rank), `steps.analytics` (stats). Every key is in [configuration](configuration.md).

## ingest

Fetches every enabled source in `sources.yaml` and stores new stories. Source types: `rss` (a feed), `listing` (an HTML list page plus a CSS selector for the article links) and `json` (a public JSON endpoint). Article text is extracted from the page, or taken from the feed entry (`text_from: feed`). The fetcher identifies itself with `channel.user_agent`, follows `robots.txt` for `channel.robots_agent`, strips tracking parameters from URLs and counts a failing source without stopping the others. `sources.yaml` is validated on load, with errors that name the entry and key.

## assess

Scores stories that have not been assessed, for the day the slot publishes, with the channel's `prompts/assess.md`. `pick` assesses what is left itself, so the step may be omitted for the same result; it exists so a digest chain (`ingest, assess`) can stop there.

## pick

Chooses the story for a slot: the queue and ranking, the `steps.pick` policy (topic mix, an optional evening batch) and, if the channel asks for one, a menu a person answers. Makes the `Story` (its text is the source the later steps check against).

## brief

Builds the facts sheet for the picked story with `prompts/brief.md`, once per pick. The brief is what the writer and the checker both see besides the source.

## write

The first script pass: brief to a plain draft (`prompts/story.md`, model role `writer`). Length limits and checks are `steps.script`. If the attempt breaks a rule, it is retried inside the pass.

## style

The second pass: draft to the styled, parsed script (role `stylist`; `prompts/punchup.md`, or `prompts/straight.md` when the channel writes straight). The script is in the engine's markup (`media/script_markup.py`): sentences, optional display/spoken forms for the TTS, `@fx` graphic directives and the closing tag line.

## proofread

Grammar, spelling and phrasing of the script (role `proofreader`, `prompts/proofread.md`). The result is taken only when code guards pass (for example, nothing factual may change). Off in the example channel: `steps.proofread.enabled: false`.

## factcheck

Compares the script with the draft sentence by sentence (the draft lock), then asks the checker for every factual claim, a verdict, an evidence quote and a note. It *judges* the Script and the Draft: failures go back through the repair loop, at most `steps.factcheck.repair_rounds` times. Details: [fact-checking](fact-checking.md).

## cards

The end card's lines (`prompts/endcard.md`), written, fact-checked with a repair round of their own; a line that still fails is left off. The card's layout and footer are channel data (`graphics/outro.json`).

## fx

Fact graphics that the script asks for with `@fx` directives are checked against the source; a failing graphic is left out of the render, not repaired.

## caption

The post's hook, title and hashtags (`prompts/caption.md`), unless the script already has them.

## save

Writes the files the renderer reads (the script, its card and fx files), records their path and marks the pick scripted.

## render

Renders the reel and its cover through the configured renderer ([rendering](rendering.md)), including the voice and images. A failed render ends the run; `steps.produce.render_attempts` sets the tries.

## qa

Automatic checks on the render: frames (a vision check against `prompts/qa_frame.md`), audio and captions. A bad image is re-rolled and the reel rendered again.

## review

Sends the reel, caption and a status block with buttons to the review channel, with the fact-check and QA flags ([review and notifications](review-and-notify.md)). In the daily run the decision comes back to the approval bot. The step is skipped in a dry run.

## publish

Creates the posts on every target once the review is approved; until then the step reports *wait* and the run stops cleanly. Skipped in a dry run. See [publishing](publishing.md).

## stats

Collects the posts' numbers at the due snapshot times (`steps.analytics.snapshots_h`) through the stats collectors.

## rank

From the stats: each reel's retention line after a delay and the week's hook examples (winners and flops) that `write` shows the model.

## voice

Speech from a text through the TTS role. A library step: the engine's own render makes its voice per sentence with the lip-sync data, so the default chain does not include it.

## Writing your own step

```python
# my-channel/plugins/legal.py
from reelline.pipeline.step import Artifact, Context, Step, StepResult

class Legal(Step):
    name = "legal"
    needs = frozenset({Artifact.SCRIPT})
    judges = frozenset({Artifact.SCRIPT})   # a check: rejected scripts go back to the step that made them

    def run(self, ctx: Context) -> StepResult:
        text = ctx[Artifact.SCRIPT].text
        bad = ["guaranteed returns"] if "guaranteed" in text else []
        return StepResult(rejects={Artifact.SCRIPT: bad} if bad else {})
```

```yaml
pipeline: [..., style, legal, factcheck, ...]
steps:
  legal: { use: "plugins.legal:Legal", repair_rounds: 2 }
```
