"""`reelline init`: a new channel from channels/example, and what a channel without graphics data of its own gets."""

import pytest
from reelline import cli
from reelline.config import load_config
from reelline.core.errors import ConfigError
from reelline.init import scaffold
from reelline.paths import DEFAULT_CHANNEL_DIR, REPO_ROOT, graphics_file, mascot_file

EXAMPLE = REPO_ROOT / "channels" / "example"


def test_init_copies_the_example_with_the_name_filled_in(tmp_path):
    dest = scaffold(tmp_path / "acme-news")
    config = load_config(dest, use_env=False)
    assert config.channel.name == "acme-news" and config.channel.display_name == "Acme News"
    assert config.channel.language == "en" and config.models.tts.voice_id_env == "ACME_NEWS_VOICE_ID"
    assert "Example Explainer" not in (dest / "prompts" / "caption.md").read_text(encoding="utf-8")
    assert "#acme-news" in (dest / "prompts" / "caption.md").read_text(encoding="utf-8")
    assert "ACME_NEWS_VOICE_ID=" in (dest / ".env.example").read_text(encoding="utf-8")
    assert cli.doctor(config, secrets=False) == []
    # The same files as the example (the voice, the host, the background), none of a run's.
    assert (dest / "assets" / "rig.yaml").read_text(encoding="utf-8").count("acme-news-host") == 1
    assert (dest / "assets" / "backgrounds" / "studio.png").is_file()
    assert not (dest / "data").exists() and not (dest / "out").exists()


def test_init_names_the_channel_after_the_directory_or_by_name(tmp_path):
    assert load_config(scaffold(tmp_path / "x", "tiny_tv"), use_env=False).channel.name == "tiny_tv"


@pytest.mark.parametrize("name", ["Acme", "1news", "a b", ""])
def test_init_refuses_a_bad_name(tmp_path, name):
    with pytest.raises(ConfigError, match="channel name"):
        scaffold(tmp_path / "d", name or "-")


def test_init_refuses_a_directory_that_has_files(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "notes.txt").write_text("mine")
    with pytest.raises(ConfigError, match="isn't empty"):
        scaffold(tmp_path / "d", "ok")
    assert (tmp_path / "d" / "notes.txt").read_text() == "mine"


def test_the_cli_init(tmp_path, capsys):
    assert cli.main(["init", str(tmp_path / "from-cli")]) == 0
    assert "from-cli" in capsys.readouterr().out and (tmp_path / "from-cli" / "config.yaml").is_file()
    assert cli.main(["init", str(tmp_path / "from-cli")]) == 1
    assert "isn't empty" in capsys.readouterr().err


def test_the_example_is_never_the_implicit_channel_beside_a_real_one(tmp_path, monkeypatch):
    import reelline.config as rc

    for name in ("example", "mine"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "config.yaml").write_text("", encoding="utf-8")
    monkeypatch.setattr(rc, "channels_root", lambda: tmp_path)
    assert rc.default_channel_dir({}).name == "mine"


def test_a_channel_without_graphics_data_or_a_mascot_gets_the_engines(tmp_path):
    for name in (
        "fx.json",
        "outro.json",
        "destinations.json",
        "georgia.json",
        "motion.json",
        "angles.json",
        "captions.json",
    ):
        assert graphics_file(tmp_path, name) == DEFAULT_CHANNEL_DIR / "graphics" / name
        assert graphics_file(EXAMPLE / "graphics", name) == DEFAULT_CHANNEL_DIR / "graphics" / name
    assert mascot_file(EXAMPLE, "Mascot.tsx") == DEFAULT_CHANNEL_DIR / "remotion" / "Mascot.tsx"
    # A channel's own: its files, not the default's.
    mine = tmp_path / "mine"
    (mine / "graphics").mkdir(parents=True)
    (mine / "remotion").mkdir()
    (mine / "graphics" / "fx.json").write_text("{}", encoding="utf-8")
    (mine / "remotion" / "Mascot.tsx").write_text("", encoding="utf-8")
    assert graphics_file(mine / "graphics", "fx.json") == mine / "graphics" / "fx.json"
    assert mascot_file(mine, "Mascot.tsx") == mine / "remotion" / "Mascot.tsx"


def test_an_svg_rig_has_nothing_to_stage_and_an_images_rig_needs_its_drawings(tmp_path):
    from reelline.render.base import RigError, load_rig

    rig = load_rig(EXAMPLE / "assets")
    assert rig.kind == "svg" and rig.drawings == () and rig.mouth_files() == []
    rig.stage(tmp_path)
    assert list(tmp_path.glob("*.png")) == [] and list((tmp_path / "mouths").iterdir()) == []
    images = tmp_path / "rig"
    images.mkdir()
    (images / "anchors.json").write_text("{}")
    (images / "rig.yaml").write_text("name: x\nanchors: anchors.json\n")
    with pytest.raises(RigError, match="drawings is required"):
        load_rig(images)
    (images / "rig.yaml").write_text("name: x\nkind: vector\nanchors: anchors.json\n")
    with pytest.raises(RigError, match="kind must be images or svg"):
        load_rig(images)
