"""Shared pytest fixtures and their helpers (registered by conftest.py)."""

import pytest
from PIL import Image
from reelline.media import backgrounds as bgmod
from reelline.pipeline.caption import write_caption
from tests.fx_script import FINAL, FakeClaude, FakeGemini, chain_route, run_chain

SIZE = tuple(bgmod.BACKGROUNDS["size"])


@pytest.fixture
def folder(tmp_path, monkeypatch):
    path = tmp_path / "backgrounds"
    path.mkdir()
    monkeypatch.setattr(bgmod, "BACKGROUNDS_DIR", path)
    for name in ("talkshow", "bedroom_night", "_draft", "office_night"):
        Image.new("RGB", SIZE).save(path / f"{name}.png")
    return path


def scripted(w):
    from reelline.pipeline.script import finish

    run = run_chain(w, FakeClaude(route=chain_route([FINAL])), FakeGemini())
    finish(w.conn, run)
    return run.script_id


@pytest.fixture
def ready(world, folder):
    """A scripted pick with caption fields, at 2026-10-01 13:00."""
    w = world
    w.script_id = scripted(w)
    write_caption(w.conn, FakeClaude(route=chain_route([FINAL])), w.script_id)
    return w
