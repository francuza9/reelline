"""docs/configuration.md is generated from the schema (`reelline config docs`): it must be up to date."""

import re

from reelline.cli import main
from reelline.config.docs import render
from reelline.paths import REPO_ROOT


def test_configuration_md_is_up_to_date():
    committed = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert committed == render(), "regenerate: reelline config docs > docs/configuration.md"


def test_reference_covers_every_section_and_names_no_model():
    text = render()
    for section in ("channel", "models", "steps.factcheck", "review", "publish", "storage", "costs", "render"):
        assert f"### `{section}`" in text
    assert not re.search(r"claude-|gemini-", text)


def test_cli_prints_it(capsys):
    assert main(["config", "docs"]) == 0
    assert capsys.readouterr().out == render()
