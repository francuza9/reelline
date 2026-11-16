"""Shared test setup: no test touches a live database, and
publishing is off unless a test turns it on, and tests run in the classic style unless one
sets another (reelline/media/style.py), and no script gets a flirt line unless a test asks for one,
and a retried API call never sleeps (retry_waits records the waits).

Every test runs with the test channel's config as its config.yaml has it, without the env layers
(no REELLINE__* from the shell or the laptop's .env): set once here, before any test module is
imported; a test changes a setting with monkeypatch.setattr(conf().<section>, "<key>", value).

reelline/pipeline/db.py record_manual() (the dev CLIs' cost recording) defaults to
storage.db_path, so a test that runs one of those CLIs' functions would write to the live ledger:
storage.db_path points to a temp file here, and connecting to the live path fails the test."""

import importlib.util
import os
from pathlib import Path

import pytest
from reelline.config import conf, load_config, set_config
from reelline.paths import REPO_ROOT

# Fixtures shared by several test modules, each module defining its own fakes and helpers.
_PLUGINS = [
    "tests.fx_chain",  # world: a picked story ready for the script chain
    "tests.fx_pick",  # pick_world, two_slots
    "tests.fx_produce",  # folder, ready
    "tests.fx_media",  # reel, video, fake_npx, straight
    "tests.fx_review",  # b: the approval bot
    "tests.fx_publish",  # enabled
    "tests.fx_stats",  # conn
]
# The public export ships only the helpers that are clean and whose own imports ship too.
pytest_plugins = [m for m in _PLUGINS if importlib.util.find_spec(m) is not None]

# The private repo tests against its own channel's config (the one that is not the example); the public export has
# only the example channel and tests against that. REELLINE_TEST_CHANNEL=example runs the example here too.
_OWN = sorted(p.name for p in (REPO_ROOT / "channels").iterdir() if p.is_dir() and p.name != "example")
TEST_CHANNEL = os.environ.get("REELLINE_TEST_CHANNEL") or (_OWN[0] if _OWN else "example")
set_config(load_config(REPO_ROOT / "channels" / TEST_CHANNEL, use_env=False))
LIVE_DB = conf().storage.db_path


@pytest.fixture(autouse=True)
def publishing_off(monkeypatch):
    """Tests never publish by default, whatever publish.enabled says in config; the
    ones about publishing switch it on themselves."""
    monkeypatch.setattr(conf().publish, "enabled", False)


@pytest.fixture(autouse=True)
def no_hook_mix(monkeypatch):
    """The hook weight is 0 in tests, so scores stay the base scores they assert; the tests
    about the hook set it (tests/test_pick_hooks.py)."""
    monkeypatch.setattr(conf().steps.pick, "hook_weight", 0)


@pytest.fixture(autouse=True)
def classic_style(monkeypatch):
    """Tests run in the classic style (the jokes, the flirt roll, the cold open as gags.json says, 2-4
    cards), whatever reelline/media/style.py STYLE_MODE ships with; tests/test_straight.py and the other tests
    about a style set it themselves."""
    from reelline.media import style

    monkeypatch.setattr(style, "STYLE_MODE", "classic")


@pytest.fixture(autouse=True)
def no_flirt_roll(monkeypatch):
    """Scripts get no flirt line unless a test asks for one (the real roll is random,
    steps.script.flirt_probability): tests/test_flirt.py sets it."""
    monkeypatch.setattr(conf().steps.script, "flirt_probability", 0.0)


@pytest.fixture(autouse=True)
def no_cold_open(monkeypatch):
    """No test reel opens on a gag unless the test asks for one, whatever gags.json's mode (and the
    style, off in the straight one since 2026-10-31): a gag's sounds aren't in their fake SFX
    libraries. tests/test_gags.py sets the mode (or --gag) itself."""
    from dataclasses import replace

    from reelline.media import gags

    off = replace(gags.load(), mode="off")
    monkeypatch.setattr(gags, "load", lambda: off)


@pytest.fixture(autouse=True)
def no_live_database(tmp_path, monkeypatch):
    from reelline.pipeline import db

    live = Path(LIVE_DB).resolve()
    real_connect = db.connect

    def guarded(path=None, *args, **kwargs):
        path = path or conf().storage.db_path
        if Path(path).resolve() == live:
            raise AssertionError(f"a test tried to open the live database {live}")
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(conf().storage, "db_path", tmp_path / "default.db")
    monkeypatch.setattr(db, "connect", guarded)


@pytest.fixture(autouse=True)
def retry_waits(monkeypatch):
    """A dropped connection's retry (reelline/providers/retry.py) never sleeps in a test: the
    waits it would have made, in order."""
    from reelline.providers import retry

    waits: list[float] = []
    monkeypatch.setattr(retry, "sleep", waits.append)
    return waits


@pytest.fixture(autouse=True)
def no_live_stats_apis(monkeypatch):
    """Stats never reach a real API in a test (reelline/pipeline/stats.py real_apis)."""
    from reelline.pipeline import stats

    def refuse(conn):
        raise AssertionError("a test tried to build the live stats API clients")

    monkeypatch.setattr(stats, "real_apis", refuse)
