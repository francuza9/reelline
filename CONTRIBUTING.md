# Contributing

Thanks for helping. This is the short version; open an issue first for anything large.

## Development setup

You need Python 3.12 or newer, Node 24 (for the renderer's tests) and ffmpeg.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r engine/requirements.txt -r engine/requirements-dev.txt
pip install --no-deps -e engine
(cd engine/remotion && npm ci)
pip install pre-commit && pre-commit install
```

## Tests

```sh
python -m pytest engine/tests -q
```

The tests use fakes for every model, platform and chat; none calls a real API (the golden test of the example channel blocks sockets). The golden test needs Node and `npm ci` in `engine/remotion`. A new feature comes with a test; a bug fix comes with the test that failed.

## Style and checks (the same as CI)

```sh
ruff check --config engine/pyproject.toml engine
ruff format --check --config engine/pyproject.toml engine
(cd engine && mypy)                      # the strict set; the rest of the package is checked report-only
python engine/src/reelline/dev/leak_guard.py     # or: reelline dev leak-guard
(cd engine/remotion && npx tsc --noEmit)
```

- Line length 120. New modules in the strict set (see `[tool.mypy]` in `engine/pyproject.toml`) must be fully typed.
- The **leak guard** keeps channel-specific names, hosts, emails, phone numbers, chat ids, private URLs and big files out of `engine/`. Do not add allowlist entries to get past it; entries only shrink.
- Settings belong in the schema (`engine/src/reelline/config/schema/`) with a default that is right for a generic channel. After changing the schema, regenerate the reference: `reelline config docs > docs/configuration.md` (a test fails when it is stale), and the committed `.env.example` files with `reelline config env-example --channel <dir>`.
- Tests that need a setting change it with `monkeypatch.setattr(conf().<section>, "<key>", value)`.

## pre-commit

`.pre-commit-config.yaml` runs ruff, ruff format and the leak guard on commit, and mypy and a fast test subset on push.

## Pull requests

Keep a pull request to one change, describe what and why, and tick the checklist in the template. By contributing you agree that your contribution is licensed under the Apache License 2.0.
