# Costs

reelline keeps a ledger of every paid model call so that what a reel costs is a query, not a guess.

## The ledger

Every call through a provider goes through one cost module (`reelline/providers/costs.py`). It records a row in the `llm_calls` table: time, provider, model, step and purpose, the tokens in, out and thinking, the cost, and what it belongs to (pick, script, reel, job). Every try is recorded: a failed one with its error and try number, the answer with the try that answered. Calls made during rendering (images, voice, vision checks) are recorded as they happen, so a process killed mid-render loses no spend. A dry run or a test records nothing.

## Price tables

Prices are config, not code: `models.prices` maps a model id to USD per million tokens, `input` and `output` (thinking is billed as output), and `output_image` for image models. The config refuses to load when a role's model, or its refusal fallback, has no row. A call whose model has no price is recorded without a cost and with a warning.

```yaml
models:
  prices:
    <model-id>: { input: 0.0, output: 0.0 }
    <image-model-id>: { input: 0.0, output: 0.0, output_image: 0.0 }
```

Fill the numbers from your provider's current price list; the engine ships no prices to rely on.

## Reports

```sh
python -m reelline.pipeline.costs report [YYYY-MM]    # the report, as /costs in the approval bot sends it
python -m reelline.pipeline.costs csv [YYYY-MM] [-o FILE]
python -m reelline.pipeline.costs total [YYYY-MM]     # the total two ways: the report's and a direct SQL sum
```

Months are calendar months in the channel's local time (`channel.utc_offset_h`). The report gives the total and the change against the previous month; for the current month, the spend so far and a pace estimate; the spend by provider, by step, and by outcome; the top reels; and the daily totals.

Every row counts once, under one outcome: *delivered* (the reel was approved or published), *waste* (rejected, superseded, expired, a failed render, a pick skipped or dropped, a failed script), *pending* (still in review), *overhead* (assess, which works on many stories at once), *manual* (a developer's own run) or *unlinked*. The cost per approved reel is the month's spend over the reels approved in it, waste and overhead included, manual spend left out so test runs do not count as a reel's cost.

Labels and groupings (which purposes make a step, which models make a provider, the outcome names, the CSV columns) are `costs.*` in the config, so a channel can rename or regroup them.

## Keeping spend down

There is no hard spend cap in the engine; the ways to bound cost are the settings that bound work:

- give each role the cheapest model that does its job, and check it ([providers](providers.md), and a planted-error test for the checker in [fact-checking](fact-checking.md));
- `steps.factcheck.repair_rounds`, `steps.produce.render_attempts`, `steps.run_daily.max_resumes` and `models.retry` bound repeats;
- `review.approve` has a regeneration limit, so a person cannot loop new scripts forever;
- leave out steps you do not need (`proofread`, `cards`, `fx`, `qa` each add calls);
- run `reelline run --dry-run` and read `costs report` after the first days: it shows which step dominates.
