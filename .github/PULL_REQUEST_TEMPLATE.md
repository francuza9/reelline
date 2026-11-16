## What and why

<!-- One change per pull request. Link the issue if there is one. -->

## Checklist

- [ ] Tests added or updated; `python -m pytest engine/tests -q` passes
- [ ] `ruff check`, `ruff format --check`, `mypy` (strict set) and the leak guard pass
- [ ] Settings are in the schema; `docs/configuration.md` regenerated (`reelline config docs`) if it changed
- [ ] Docs updated (`docs/`, README) if behaviour or a setting changed
- [ ] No credentials, private names, hosts or ids in the change
