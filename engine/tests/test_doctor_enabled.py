"""doctor asks only for the credentials of adapters the config enables."""

import pytest
from reelline import cli
from reelline.config import load_config, set_config

# A channel with every adapter on: the engine's defaults (Telegram review and notify, Buffer and Meta targets, an S3
# blob store, Claude and Gemini roles) plus a credits page.
CONFIG = "publish:\n  credits_page:\n    project: pages-project\n"
_dir = {}


@pytest.fixture(autouse=True)
def full_channel(tmp_path):
    d = tmp_path / "full"
    (d / "prompts").mkdir(parents=True)
    (d / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (d / "sources.yaml").write_text("[]\n", encoding="utf-8")
    _dir["path"] = d


TELEGRAM = "review.telegram.bot_token_env"
META = "publish.meta.page_token_env"
BUFFER = "publish.buffer.api_key_env"
R2 = "publish.r2.bucket_env"
CREDITS = "publish.credits_page.api_token_env"
GEMINI = "models.providers.google.api_key_env"


def enabled(**overrides):
    return cli.enabled_credentials(load_config(_dir["path"], overrides, use_env=False))


def test_a_channel_with_every_adapter_on_enables_everything():
    ok = enabled()
    assert all(ok(p) for p in (TELEGRAM, META, BUFFER, R2, CREDITS, GEMINI))


def test_telegram_is_not_asked_for_when_review_and_notify_use_other_channels():
    ok = enabled(**{"review.channel": "cli", "notify.channel": "log"})
    assert not ok(TELEGRAM) and not ok("review.telegram.chat_id_env")


def test_telegram_is_asked_for_when_only_one_of_review_and_notify_uses_it():
    assert enabled(**{"review.channel": "cli", "notify.channel": "telegram"})(TELEGRAM)
    assert enabled(**{"review.channel": "telegram", "notify.channel": "log"})(TELEGRAM)


def test_publish_and_storage_credentials_follow_the_targets_and_the_blob_store():
    local = {f"publish.targets.{c}": "local" for c in ("instagram", "tiktok", "youtube", "facebook")}
    local["steps.analytics.collectors"] = ["youtube"]
    ok = enabled(**local, **{"storage.blob.kind": "local"})
    assert not ok(META) and not ok(BUFFER) and not ok(R2)
    assert enabled(**local)(R2)
    assert enabled(**{**local, "publish.targets.instagram": "buffer"})(BUFFER)
    assert enabled(**{**local, "steps.analytics.collectors": ["meta"]})(META)


def test_a_provider_no_role_uses_is_not_asked_for():
    config = load_config(_dir["path"], use_env=False)
    used = {role.provider for role in config.models.roles().values()}
    assert used == {"anthropic", "google"}
    # every role on the writer's provider and model: nothing uses Google any more
    writer = config.models.writer
    ok = enabled(**{f"models.{r}": writer.model_dump() for r in config.models.roles()})
    assert ok("models.providers.anthropic.api_key_env") and not ok(GEMINI)


def test_a_credits_page_without_a_project_needs_no_cloudflare_token():
    assert not enabled(**{"publish.credits_page.project": ""})(CREDITS)


def test_doctor_does_not_list_the_credentials_of_a_disabled_adapter(tmp_path, capsys):
    env_file = tmp_path / "x.env"
    env_file.write_text("GEMINI_API_KEY=x\n", encoding="utf-8")
    config = load_config(
        _dir["path"],
        {"storage.env_file": str(env_file), "review.channel": "cli", "notify.channel": "log"},
        use_env=False,
    )
    previous = set_config(config)
    try:
        problems = cli.doctor(config)
    finally:
        set_config(previous)
    assert not any("TELEGRAM" in p for p in problems)
    assert any("BUFFER_API_KEY" in p for p in problems)  # still enabled
