"""reelline.config: the layers, the env aliases, the env file's place, the CLI."""

from pathlib import Path

import pytest
import reelline.config as rc
from reelline import cli
from reelline.config import ConfigError, load_config
from reelline.config.schema import Config
from reelline.paths import REPO_ROOT

CHANNEL_DIR = rc.conf().channel.dir  # the test channel (conftest.py)


@pytest.fixture
def channel(tmp_path):
    d = tmp_path / "demo"
    (d / "prompts").mkdir(parents=True)
    (d / "config.yaml").write_text("channel:\n  utc_offset_h: 2\nsteps:\n  pick:\n    menu_size: 4\n", encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def keep_config():
    """cli.main() sets the config it loaded: put the test session's back."""
    previous = rc.conf()
    yield
    rc.set_config(previous)


def test_schema_defaults_are_generic():
    c = Config()
    assert c.channel.user_agent == "reelline/{version}" and c.publish.enabled is False
    assert c.publish.credits_page.domain == "" and c.steps.caption.required_hashtags == ()
    assert c.steps.script.flirt_probability == 0.0 and c.models.tts.voice_id_env == "TTS_VOICE_ID"


def test_layers_last_wins(channel, tmp_path):
    env_file = tmp_path / "x.env"
    env_file.write_text("REELLINE__STEPS__PICK__MENU_SIZE=6\nREELLINE__CHANNEL__UTC_OFFSET_H=3\n", encoding="utf-8")
    environ = {"REELLINE__STORAGE__ENV_FILE": str(env_file), "REELLINE__CHANNEL__UTC_OFFSET_H": "5"}
    assert load_config(channel, use_env=False).steps.pick.menu_size == 4  # yaml over default
    c = load_config(channel, environ=environ)
    assert c.steps.pick.menu_size == 6 and c.channel.utc_offset_h == 5  # env file, then process env
    c = load_config(channel, {"steps.pick.menu_size": 9, "channel": {"slots": ["09:00"]}}, environ=environ)
    assert c.steps.pick.menu_size == 9 and c.channel.slots == ("09:00",) and c.channel.utc_offset_h == 5


def test_env_values_read_as_yaml(channel):
    c = load_config(
        channel,
        environ={
            "REELLINE__CHANNEL__SLOTS": "['13:00', '20:00']",
            "REELLINE__PUBLISH__ENABLED": "true",
            "REELLINE__RENDER__REMOTION_CONCURRENCY": "2",
        },
    )
    assert c.channel.slots == ("13:00", "20:00") and c.publish.enabled is True and c.render.remotion_concurrency == 2


def test_unknown_key_fails(channel):
    with pytest.raises(ConfigError, match="menu_sise"):
        load_config(channel, {"steps.pick.menu_sise": 3}, use_env=False)
    with pytest.raises(ConfigError, match="nope"):
        load_config(channel, environ={"REELLINE__NOPE": "1"})


def test_paths_are_relative_to_the_channel(channel):
    c = load_config(channel, {"storage.log_dir": "../logs"}, use_env=False)
    assert c.storage.db_path == channel / "data" / "reelline.db"
    assert c.storage.log_dir == channel.parent / "logs" and c.steps.script.story_prompt == channel / "prompts/story.md"
    assert c.channel.name == "demo" and c.channel.dir == channel


def test_channel_dir_is_set_by_the_loader(channel):
    (channel / "config.yaml").write_text("channel:\n  dir: /elsewhere\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="channel.dir"):
        load_config(channel, use_env=False)


def test_default_channel(tmp_path, monkeypatch):
    assert rc.default_channel_dir({"REELLINE_CHANNEL": str(tmp_path)}) == tmp_path
    monkeypatch.setattr(rc, "channels_root", lambda: tmp_path)
    with pytest.raises(ConfigError, match="0 channels"):
        rc.default_channel_dir({})
    (tmp_path / "solo").mkdir()
    (tmp_path / "solo" / "config.yaml").write_text("{}\n", encoding="utf-8")
    assert rc.default_channel_dir({}) == tmp_path / "solo"  # the one channel


def test_env_file_place(channel, tmp_path):
    assert rc.default_env_file(channel) == REPO_ROOT / ".env"  # none in the channel: the repo's
    (channel / ".env").write_text("", encoding="utf-8")
    assert rc.default_env_file(channel) == channel / ".env"
    assert rc.env_file_from({"REELLINE__STORAGE__ENV_FILE": "/a/b.env"}) == Path("/a/b.env")
    with pytest.raises(ConfigError, match="absolute"):
        rc.env_file_from({"REELLINE__STORAGE__ENV_FILE": "rel.env"})
    c = load_config(channel, environ={"REELLINE__STORAGE__ENV_FILE": "/a/c.env"})
    assert c.storage.env_file == Path("/a/c.env")


def test_script_model_needs_prices(channel):
    with pytest.raises(ConfigError, match="no price"):
        load_config(channel, {"models.writer.refusal_fallback": "claude-x"}, use_env=False)


def test_set_config_injects():
    mine = Config()
    previous = rc.set_config(mine)
    try:
        assert rc.conf() is mine
    finally:
        rc.set_config(previous)


def test_show_masks_and_merges(channel, capsys):
    assert cli.main(["config", "show", "--channel", str(channel), "--set", "steps.pick.menu_size=7"]) == 0
    out = capsys.readouterr().out
    assert "menu_size: 7" in out and "api_key_env: GEMINI_API_KEY" in out
    assert cli.plain({"api_key": "abc", "api_key_env": "X", "bot_token": ""}) == {
        "api_key": cli.MASK,
        "api_key_env": "X",
        "bot_token": "",
    }


def test_committed_env_example_is_generated():
    assert (CHANNEL_DIR / ".env.example").read_text(encoding="utf-8") == cli.env_example(
        load_config(CHANNEL_DIR, use_env=False)
    )


def test_env_example_names_every_credential_once():
    text = cli.env_example(load_config(CHANNEL_DIR, use_env=False))
    names = [line.split("=")[0] for line in text.splitlines() if line and not line.startswith("#")]
    assert (
        len(names) == len(set(names)) == 38
        and "OPENAI_API_KEY" in names
        and "SLACK_WEBHOOK_URL" in names
        and rc.conf().models.tts.voice_id_env in names
        and "GEMINI_API_KEY" in names
    )


def test_doctor(channel, tmp_path, capsys):
    (channel / "sources.yaml").write_text("[]\n", encoding="utf-8")
    env_file = tmp_path / "x.env"
    env_file.write_text("GEMINI_API_KEY=secret-value\n", encoding="utf-8")
    config = load_config(channel, {"storage.env_file": str(env_file)}, use_env=False)
    previous = rc.set_config(config)
    try:
        problems = cli.doctor(config)
    finally:
        rc.set_config(previous)
    text = "\n".join(problems) + capsys.readouterr().out
    assert "secret-value" not in text and "GEMINI_API_KEY" not in text
    assert "credential missing: TELEGRAM_BOT_TOKEN (review.telegram.bot_token_env)" in problems
    assert "credential missing: PEXELS_API_KEY (optional, render.pexels.api_key_env)" not in problems  # printed only
    assert any(p.startswith("path missing: steps.script.story_prompt") for p in problems)
    assert not any("db_path" in p for p in problems)  # db.connect() creates its directory


def test_doctor_no_secrets(channel, tmp_path, capsys):
    (channel / "sources.yaml").write_text("[]\n", encoding="utf-8")
    config = load_config(channel, {"storage.env_file": str(tmp_path / "absent.env")}, use_env=False)
    previous = rc.set_config(config)
    try:
        problems = cli.doctor(config, secrets=False)
        assert any("env file" in p or "credential" in p for p in cli.doctor(config))
    finally:
        rc.set_config(previous)
    assert not any("env file" in p or "credential" in p for p in problems)
    assert any(p.startswith("path missing") for p in problems)  # the rest still runs


def test_stored_paths(tmp_path, monkeypatch):
    from reelline import paths

    root, channel = tmp_path / "repo", tmp_path / "repo" / "channels" / "demo"
    monkeypatch.setattr(paths, "REPO_ROOT", root)
    old = "content/reels/auto/2026-10-01_1300_x.txt"
    assert paths.stored_path("out/reels/a/reel.mp4", channel) == root / "out/reels/a/reel.mp4"
    assert paths.stored_path("channels/demo/content/reels/auto/b.txt", channel) == channel / "content/reels/auto/b.txt"
    assert paths.stored_path("/abs/x.mp4", channel) == Path("/abs/x.mp4")
    assert paths.stored_path(old, channel) == channel / old  # pre-split row: the channel's
    (root / old).parent.mkdir(parents=True)
    (root / old).write_text("x", encoding="utf-8")
    assert paths.stored_path(old, channel) == root / old  # only the old place has it
    (channel / old).parent.mkdir(parents=True)
    (channel / old).write_text("x", encoding="utf-8")
    assert paths.stored_path(old, channel) == channel / old  # moved: the channel's
    assert paths.stored_path("data/x.json") == rc.conf().channel.dir / "data/x.json"
