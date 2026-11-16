"""Drop-in backgrounds (reelline/media/backgrounds.py): the folder is the registry, `_` files are
disabled, validation names the file, sidecars deep-merge over screen.json with only
known keys, and render_reel.py --background-check composes a still and a cover
(Remotion and Telegram mocked)."""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image
from reelline.media import backgrounds as bgmod
from reelline.media.backgrounds import (
    BackgroundError,
    all_backgrounds,
    base_screen,
    enabled_backgrounds,
    get_background,
    orphan_sidecars,
    screen_spec,
    validate,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

SIZE = tuple(bgmod.BACKGROUNDS["size"])


def png(folder: Path, name: str, size=SIZE, mode="RGB") -> Path:
    path = folder / f"{name}.png"
    Image.new(mode, size).save(path)
    return path


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(bgmod, "BACKGROUNDS_DIR", tmp_path)
    for name in ("office_night", "bedroom_night", "_draft", "talkshow"):
        png(tmp_path, name)
    return tmp_path


def test_folder_is_the_registry(folder):
    assert [b.name for b in all_backgrounds()] == ["_draft", "bedroom_night", "office_night", "talkshow"]
    assert [b.name for b in enabled_backgrounds()] == ["bedroom_night", "office_night", "talkshow"]
    draft = get_background("_draft")
    assert not draft.enabled and draft.file == "_draft.png"
    (folder / "notes.txt").write_text("x")
    assert len(all_backgrounds()) == 4
    with pytest.raises(BackgroundError, match="No background 'nope'.*have: _draft, bedroom_night"):
        get_background("nope")


def test_the_real_folder_has_no_hardcoded_list():
    names = [b.name for b in all_backgrounds()]
    assert names == sorted(p.stem for p in bgmod.BACKGROUNDS_DIR.glob("*.png"))
    for bg in all_backgrounds():
        validate(bg)
        screen_spec(bg)
    assert orphan_sidecars() == []


@pytest.mark.parametrize(
    "size, mode, error",
    [
        ((1080, 1920), "RGBA", None),
        ((1920, 1080), "RGB", "is 1920x1080; it must be 1080x1920"),
        ((1080, 1921), "RGB", "is 1080x1921"),
        ((1080, 1920), "L", "is mode L; it must be one of RGB, RGBA"),
        ((1080, 1920), "P", "is mode P"),
    ],
)
def test_validation_names_the_file(folder, size, mode, error):
    png(folder, "odd", size, mode)
    if error is None:
        validate(get_background("odd"))
    else:
        with pytest.raises(BackgroundError, match=error) as exc:
            validate(get_background("odd"))
        assert "odd.png" in str(exc.value)


def test_unreadable_png_fails_with_its_name(folder):
    (folder / "broken.png").write_bytes(b"not a png")
    with pytest.raises(BackgroundError, match="broken.png isn't a readable image"):
        validate(get_background("broken"))


def test_no_sidecar_is_screen_json(folder):
    assert screen_spec(get_background("talkshow")) == base_screen()


def test_sidecar_deep_merges_over_screen_json(folder):
    (folder / "talkshow.json").write_text(
        json.dumps({"screen": {"y": 350, "width": 700}, "image": {"kenBurns": {"zoomTo": 1.03}}})
    )
    base, spec = base_screen(), screen_spec(get_background("talkshow"))
    assert spec["screen"]["y"] == 350 and spec["screen"]["width"] == 700
    assert spec["screen"]["x"] == base["screen"]["x"] and spec["screen"]["height"] == base["screen"]["height"]
    assert spec["image"]["kenBurns"] == {**base["image"]["kenBurns"], "zoomTo": 1.03}
    assert spec["idle"] == base["idle"]
    # Only that background: the others keep screen.json, and the base file is untouched.
    assert screen_spec(get_background("office_night")) == base == base_screen()


@pytest.mark.parametrize(
    "sidecar, error",
    [
        ({"screen": {"depth": 3}}, "unknown key 'screen.depth'"),
        ({"chair": {"x": 1}}, "unknown key 'chair'"),
        ({"image": {"kenBurns": {"zoomTo": 1.1, "speed": 2}}}, "unknown key 'image.kenBurns.speed'"),
        ({"screen": 5}, "'screen' must be an object"),
        ({"screen": {"x": {"a": 1}}}, "'screen.x' must be a value, not an object"),
    ],
)
def test_sidecar_unknown_or_misshaped_keys_fail(folder, sidecar, error):
    (folder / "talkshow.json").write_text(json.dumps(sidecar))
    with pytest.raises(BackgroundError, match=error) as exc:
        screen_spec(get_background("talkshow"))
    assert "talkshow.json" in str(exc.value)


def test_sidecar_not_json_or_not_object(folder):
    (folder / "talkshow.json").write_text("{nope")
    with pytest.raises(BackgroundError, match="talkshow.json isn't valid JSON"):
        screen_spec(get_background("talkshow"))
    (folder / "talkshow.json").write_text("[1]")
    with pytest.raises(BackgroundError, match="must be a JSON object"):
        screen_spec(get_background("talkshow"))


def test_orphan_sidecar_is_reported(folder):
    (folder / "gone.json").write_text("{}")
    assert [p.name for p in orphan_sidecars()] == ["gone.json"]


# ---- render_reel.py --background-check -----------------------------------------------------


def test_background_check_renders_still_and_cover_for_a_disabled_background(folder, tmp_path, monkeypatch):
    import render_reel

    (folder / "_draft.json").write_text(json.dumps({"screen": {"y": 400, "height": 360}}))
    monkeypatch.setattr(render_reel, "CHECK_DIR", tmp_path / "check")
    calls, sent = [], []

    def fake_remotion(args, props_json, public_dir):
        calls.append((args, json.loads(props_json.read_text(encoding="utf-8")), public_dir))
        Path(args[2]).write_bytes(b"png")

    image = {
        "src": "screen_images/x.jpg",
        "start": 0.0,
        "end": 2.0,
        "width": 1920,
        "height": 1080,
        "mode": "cover",
        "crop": {"x": 0, "y": 0, "w": 1, "h": 1},
        "focal": {"x": 0.5, "y": 0.5},
        "panelColor": None,
        "seed": "s",
    }
    seen_screens = []
    monkeypatch.setattr(render_reel, "remotion", fake_remotion)
    monkeypatch.setattr(render_reel, "sample_image", lambda scr, public_dir, end: seen_screens.append(scr) or image)
    monkeypatch.setattr(render_reel, "send_photo", lambda path, caption: sent.append((path.name, caption)))

    still, cover, spec = render_reel.background_check("_draft")
    assert spec["screen"]["y"] == 400
    (still_args, still_props, public_dir), (cover_args, cover_props, _) = calls
    assert still_args[:2] == ["still", "Reel"] and cover_args[:2] == ["still", "Cover"]
    assert still_props["background"] == cover_props["background"] == "_draft.png"
    assert still_props["screen"] == cover_props["screen"] == spec
    assert still_props["mood"] == cover_props["mood"] == render_reel.DEFAULT_MOOD
    assert still_props["screenImages"] == [image] and cover_props["image"] == image
    (chunk,) = still_props["captions"]
    assert len(chunk["lines"]) == 2
    assert cover_props["headline"] == bgmod.BACKGROUNDS["check"]["headline"]
    # Only the checked background is staged; the TV face comes from the merged spec.
    assert [p.name for p in (public_dir / "backgrounds").iterdir()] == ["_draft.png"]
    assert seen_screens[0].face_h == 360 - 2 * base_screen()["screen"]["bezelPx"]
    assert [name for name, _ in sent] == ["still.png", "cover.png"]
    assert "disabled" in sent[0][1] and "_draft.json" in sent[0][1]


def test_background_check_fails_loudly_on_an_invalid_background(folder, monkeypatch):
    import render_reel

    png(folder, "small", (720, 1280))
    monkeypatch.setattr(render_reel, "remotion", lambda *a: pytest.fail("rendered an invalid background"))
    with pytest.raises(render_reel.RenderError, match="small.png is 720x1280"):
        render_reel.background_check("small", send=False)


def test_sample_captions_are_two_timed_lines():
    import render_reel

    (chunk,) = render_reel.sample_captions([["a", "b"], ["c", "d"]], 2.0)
    assert [[w["text"] for w in line] for line in chunk["lines"]] == [["a", "b"], ["c", "d"]]
    assert chunk["lines"][1][1]["end"] == 2.0 and chunk["chunk_end"] == 2.0
