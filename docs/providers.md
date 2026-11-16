# Providers: models, voice, vision and images

The engine never talks to a vendor SDK from a step. A step asks the registry (`reelline/providers/registry.py`) for the provider of a *role* (`get_llm("writer")`, `get_vision()`, `get_image_generator()`, `get_tts()`) and calls a small protocol.

## Roles

One role per use of a model, so each job can have its own provider, model and parameters under `models.<role>`:

| Role | Used for | Kind |
|---|---|---|
| `writer` | the first script pass and its repairs | text |
| `stylist` | the second pass | text |
| `proofreader` | the proofread of the script and the caption's hook | text |
| `caption` | the caption | text |
| `endcard` | the end card's lines | text |
| `assessor` | scoring stories | text |
| `briefer` | the facts sheet | text |
| `checker` | the fact-check of script, graphics and end card | text |
| `vision` | the image picker's check of a candidate image | vision |
| `qa_vision` | QA's look at rendered frames | vision |
| `illustrator` | generated fallback images | image generation |
| `tts` | the voice | speech |

A role's keys (`provider`, `model`, `max_tokens`, `temperature`, `seed`, `thinking`, `effort`, `refusal_fallback`, `transport_retry`, ...) are in [configuration](configuration.md). A role given in part keeps the engine's default for the rest, unless it names another provider, in which case it is written whole. `<provider>` is one of `anthropic`, `google`, `openai`; use your own model ids:

```yaml
models:
  writer:      { provider: <provider>, model: <model-id>, max_tokens: 8000 }
  checker:     { provider: <provider>, model: <model-id>, temperature: 0 }
  assessor:    { provider: <provider>, model: <model-id> }
  prices:                              # USD per 1M tokens, one row per model used
    <model-id>: { input: 0.0, output: 0.0 }
```

## Provider connections

`models.providers.<name>` holds a connection: the adapter `type` (`anthropic`, `google` or `openai`), an optional `base_url` and `timeout_s`, and `api_key_env`, the *name* of the variable in `.env` that holds the key. The key is read from the channel's `.env` file each time a connection is made (a long-running bot picks up a new key) and never from the shell environment.

| Adapter | Text | Vision | Image generation | Speech |
|---|---|---|---|---|
| `anthropic` | yes | no | no | no |
| `google` | yes | yes | yes | yes |
| `openai` | yes, for any OpenAI-compatible endpoint (set `base_url`) | yes | no | no |

So the `illustrator` and `tts` roles need the `google` adapter today. SDKs are imported lazily: install only the extras you use (`pip install -e "engine[anthropic,google]"`).

## Prices, retries, refusals

- Every model a role (or a refusal fallback) names must have a row in `models.prices`; the config refuses to load otherwise. See [costs](costs.md).
- A dropped connection is retried `models.retry.attempts` times in all, waiting `models.retry.waits_s` between tries (a role may opt out with `transport_retry: false`). HTTP 429 and 5xx answers are retried per role with `api_retries` / `api_retry_wait_s` (google).
- `refusal_fallback` (anthropic): a request the model declines is sent once to the named model.
- `thinking_alert_share`: a call whose thinking used more than this share of `max_tokens` is logged and alerted.

## Adding an adapter

1. Write `reelline/providers/<name>.py`: a `connect(settings)` that builds the client, and a class taking `(role, spec, client)` that implements the protocols the roles on it use (`LLMProvider`: `complete`, `complete_json`; `VisionChecker`: `look`; `ImageGenerator`: `generate`; `TTSProvider`: `synthesize`). Subclass the shared `Adapter` in `providers/base.py`; send every call through the retry policy (`providers/retry.py`) and record it with the `CostLedger` (`providers/costs.py`) so costs appear in the ledger.
2. Raise `LLMError` (`RefusalError` for refusals, `TransportFailure` when the connection dropped on every try) so steps and the repair loop handle failures the same way for every adapter.
3. Register it: a branch in `_adapter()` in `providers/registry.py`, the new `type` in `ProviderConf` and a `models.providers.<name>` section in the schema (with its `api_key_env` field).
4. Test it with the shared fakes (`providers/fakes.py`: `FakeLLM` answers by purpose) and add a case to the provider tests.

Tests of the chain never call a real provider: `FakeLLM`, `FakeTTS` and friends take their place (`Context.clients`, or the `given` argument of the getters).
