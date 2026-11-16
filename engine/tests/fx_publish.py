"""Shared pytest fixtures and their helpers (registered by conftest.py)."""

import pytest
from reelline.config import conf

GOOD = {"duration": 43.2, "video": {"codec": "h264", "width": 1080, "height": 1920, "fps": 30.0}, "audio": True}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(conf().publish, "enabled", True)
    monkeypatch.setattr("reelline.pipeline.qa.probe", lambda mp4: GOOD)
