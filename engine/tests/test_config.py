from pathlib import Path

import pytest
from reelline.config import conf
from reelline.config import credentials as config
from reelline.config.credentials import ConfigError, load_settings
from reelline.paths import REPO_ROOT
from reelline.providers import connection

FILE_KEY = "file-key-AIzaSyTESTVALUE0123456789"
SHELL_KEY = "shell-key-SHOULD-NEVER-BE-USED"
VOICE_ID = "voice_test123"
# The channel's own variable names (models.tts.voice_id_env, ...api_key_env), whatever the channel is called.
VOICE_ENV = conf().models.tts.voice_id_env
ANTHROPIC_ENV = conf().models.providers.anthropic.api_key_env


def write_env(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "REELLINE__STORAGE__ENV_FILE",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        VOICE_ENV,
        "GOOGLE_GENAI_USE_VERTEXAI",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = write_env(
        tmp_path / "channel.env",
        f"GEMINI_API_KEY={FILE_KEY}\n{VOICE_ENV}={VOICE_ID}\n",
    )
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    return path


def test_default_path_is_repo_root_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert config.resolve_env_file() == REPO_ROOT / ".env"


def test_missing_file_error_names_path(tmp_path, monkeypatch):
    missing = tmp_path / "nope.env"
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(missing))
    with pytest.raises(ConfigError, match="not found") as exc:
        load_settings()
    assert str(missing) in str(exc.value)


@pytest.mark.parametrize(
    "body, missing_key",
    [
        (f"{VOICE_ENV}={VOICE_ID}\n", "GEMINI_API_KEY"),
        (f"GEMINI_API_KEY=\n{VOICE_ENV}={VOICE_ID}\n", "GEMINI_API_KEY"),
        (f"GEMINI_API_KEY=   \n{VOICE_ENV}={VOICE_ID}\n", "GEMINI_API_KEY"),
        (f"GEMINI_API_KEY\n{VOICE_ENV}={VOICE_ID}\n", "GEMINI_API_KEY"),
        (f"GEMINI_API_KEY={FILE_KEY}\n", VOICE_ENV),
        (f"GEMINI_API_KEY={FILE_KEY}\n{VOICE_ENV}=\n", VOICE_ENV),
    ],
    ids=["key-absent", "key-empty", "key-blank", "key-no-equals", "voice-absent", "voice-empty"],
)
def test_missing_or_empty_key_is_named(tmp_path, monkeypatch, body, missing_key):
    path = write_env(tmp_path / "channel.env", body)
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    with pytest.raises(ConfigError) as exc:
        load_settings()
    message = str(exc.value)
    assert missing_key in message
    assert str(path) in message
    assert FILE_KEY not in message


def test_both_keys_missing_are_both_named(tmp_path, monkeypatch):
    path = write_env(tmp_path / "channel.env", "OTHER=1\n")
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "GEMINI_API_KEY" in str(exc.value)
    assert VOICE_ENV in str(exc.value)


def test_shell_key_does_not_override_file(env_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SHELL_KEY)
    monkeypatch.setenv(VOICE_ENV, "shell_voice")
    settings = load_settings()
    assert settings.gemini_api_key == FILE_KEY
    assert settings.voice_id == VOICE_ID


def test_shell_key_does_not_substitute_for_empty_file_value(tmp_path, monkeypatch):
    path = write_env(tmp_path / "channel.env", f"{VOICE_ENV}={VOICE_ID}\n")
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    monkeypatch.setenv("GEMINI_API_KEY", SHELL_KEY)
    with pytest.raises(ConfigError, match="GEMINI_API_KEY") as exc:
        load_settings()
    assert SHELL_KEY not in str(exc.value)


def test_file_values_are_not_interpolated_from_shell(tmp_path, monkeypatch):
    path = write_env(
        tmp_path / "channel.env",
        f"GEMINI_API_KEY=${{GEMINI_API_KEY}}\n{VOICE_ENV}={VOICE_ID}\n",
    )
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    monkeypatch.setenv("GEMINI_API_KEY", SHELL_KEY)
    assert load_settings().gemini_api_key != SHELL_KEY


def test_load_does_not_touch_os_environ(env_file):
    import os

    load_settings()
    assert "GEMINI_API_KEY" not in os.environ
    assert VOICE_ENV not in os.environ


def test_env_file_override_is_honored(env_file):
    settings = load_settings()
    assert settings.env_file == env_file
    assert settings.gemini_api_key == FILE_KEY
    assert settings.voice_id == VOICE_ID


def test_relative_override_is_rejected(monkeypatch):
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", "relative/channel.env")
    with pytest.raises(ConfigError, match="absolute"):
        load_settings()


def test_settings_is_frozen_and_repr_hides_key(env_file):
    settings = load_settings()
    with pytest.raises(AttributeError):
        settings.gemini_api_key = "x"
    assert FILE_KEY not in repr(settings)
    assert FILE_KEY not in str(settings)


def test_client_uses_file_key_even_with_shell_overrides(env_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SHELL_KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", SHELL_KEY)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    client = connection("checker")
    assert client._api_client.api_key == FILE_KEY
    assert client._api_client.vertexai is False


# --- per-service accessors ---------------------------------------------------------


def test_service_accessor_requires_only_its_own_keys(tmp_path, monkeypatch):
    path = write_env(tmp_path / "channel.env", "TELEGRAM_BOT_TOKEN=tok-SECRET\nTELEGRAM_CHAT_ID=42\n")
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    telegram = config.load_telegram()
    assert telegram.chat_id == "42"
    assert "tok-SECRET" not in repr(telegram)
    with pytest.raises(ConfigError, match="PEXELS_API_KEY"):
        config.load_pexels()
    with pytest.raises(ConfigError, match="PIXABAY_API_KEY"):
        config.load_pixabay()
    path.write_text(path.read_text() + "PIXABAY_API_KEY= px-SECRET \n")
    assert config.load_pixabay().api_key == "px-SECRET" and "px-SECRET" not in repr(config.load_pixabay())
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        config.load_gemini()


def test_service_accessor_names_every_missing_key(tmp_path, monkeypatch):
    path = write_env(tmp_path / "channel.env", "META_APP_ID=1\nMETA_PAGE_ID=\n")
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    with pytest.raises(ConfigError) as exc:
        config.load_meta()
    for key in ("META_APP_SECRET", "META_PAGE_ID", "META_PAGE_TOKEN"):
        assert key in str(exc.value)
    assert "META_APP_ID" not in str(exc.value)


def test_gemini_accessor_builds_a_client(env_file):
    client = connection("briefer")
    assert client._api_client.api_key == FILE_KEY


def test_env_example_lists_every_accessor_key():
    import re

    from reelline.cli import env_fields

    example = (conf().channel.dir / ".env.example").read_text(encoding="utf-8")
    used = {name for _, name, _, _ in env_fields(conf())}
    listed = set(re.findall(r"^([A-Z][A-Z0-9_]+)=$", example, re.M))
    # ANTHROPIC_API_KEY is only the name load_anthropic() refuses.
    assert used == listed and "ANTHROPIC_API_KEY" not in listed
    assert not re.search(r"^[A-Z0-9_]+=.+$", example, re.M), ".env.example must have empty values"


# --- Anthropic ---------------------------------------------------------------------------

ANTHROPIC_FILE_KEY = "sk-ant-file-key-TESTVALUE"


def test_anthropic_key_is_the_channels_own_and_file_only(tmp_path, monkeypatch):
    path = write_env(tmp_path / "channel.env", f"{ANTHROPIC_ENV}={ANTHROPIC_FILE_KEY}\n")
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", SHELL_KEY)
    monkeypatch.setenv(ANTHROPIC_ENV, SHELL_KEY)
    settings = config.load_anthropic()
    assert settings.api_key == ANTHROPIC_FILE_KEY and ANTHROPIC_FILE_KEY not in repr(settings)


@pytest.mark.parametrize(
    "body",
    [
        f"ANTHROPIC_API_KEY={ANTHROPIC_FILE_KEY}\n",
        f"{ANTHROPIC_ENV}={ANTHROPIC_FILE_KEY}\nANTHROPIC_API_KEY=\n",
    ],
    ids=["old-name-only", "both-names"],
)
def test_anthropic_old_key_name_must_be_renamed(tmp_path, monkeypatch, body):
    path = write_env(tmp_path / "channel.env", body)
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    with pytest.raises(ConfigError, match=f"rename it to {ANTHROPIC_ENV}") as exc:
        config.load_anthropic()
    assert ANTHROPIC_FILE_KEY not in str(exc.value)


def test_anthropic_missing_key_is_named(tmp_path, monkeypatch):
    path = write_env(tmp_path / "channel.env", "OTHER=1\n")
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    with pytest.raises(ConfigError, match=ANTHROPIC_ENV):
        config.load_anthropic()


def test_claude_client_passes_the_file_key_explicitly(tmp_path, monkeypatch):
    API_BASE_URL = conf().models.providers.anthropic.base_url

    path = write_env(tmp_path / "channel.env", f"{ANTHROPIC_ENV}={ANTHROPIC_FILE_KEY}\n")
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", SHELL_KEY)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", SHELL_KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:1")
    client = connection("writer")
    assert client.api_key == ANTHROPIC_FILE_KEY and client.auth_token is None
    assert str(client.base_url).rstrip("/") == API_BASE_URL


def test_bot_supervisor_defaults_to_the_tick():
    from reelline.pipeline.run_daily import Effects

    assert conf().steps.run_daily.bot_supervisor == "tick" and Effects().supervisor() == "tick"
