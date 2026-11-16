import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import send_to_phone
from send_to_phone import SendError, caption_lines, resolve_reel, split_caption
from tests.telegram_fakes import recording_notifier

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_resolve_by_name_dir_and_path(tmp_path, monkeypatch):
    monkeypatch.setattr(send_to_phone, "REELS_DIR", tmp_path)
    reel = tmp_path / "demo"
    reel.mkdir()
    (reel / "reel.mp4").write_bytes(b"x")
    assert resolve_reel("demo") == (reel / "reel.mp4").resolve()
    assert resolve_reel(reel) == (reel / "reel.mp4").resolve()
    assert resolve_reel(reel / "reel.mp4") == (reel / "reel.mp4").resolve()
    with pytest.raises(SendError, match="not found"):
        resolve_reel("missing")


def test_caption_lists_check_words_and_images(tmp_path):
    (tmp_path / "reel_props.json").write_text(json.dumps({"background": "office_night.png", "mood": "smug"}))
    (tmp_path / "check_these.json").write_text(
        json.dumps([{"paragraph": 2, "start": 3.25, "word": "სიტყვა", "score": 0.21}], ensure_ascii=False)
    )
    (tmp_path / "images.json").write_text(
        json.dumps(
            {
                "paragraphs": [
                    {"paragraph": 1, "status": "image", "source": "commons", "license": "CC BY-SA 4.0"},
                    {"paragraph": 2, "status": "none"},
                    {"paragraph": 3, "status": "no_candidate", "value": "office internship", "reason": "x"},
                ]
            }
        )
    )
    lines = caption_lines(tmp_path, "demo", 41.26, None)
    assert lines[0] == "demo | 41.3s | bg office_night | mood smug"
    assert "  ¶2 3.2s სიტყვა (0.21)" in lines
    assert "  ¶1 commons, CC BY-SA 4.0" in lines
    assert lines[lines.index("Images:") + 1 :] == ["  ¶1 commons, CC BY-SA 4.0", "  ¶3 no image (office internship)"]


def test_split_caption_overflows_whole_lines():
    lines = ["a" * 600, "b" * 600, "c" * 10]
    caption, rest = split_caption(lines, 1024, 4096)
    assert caption == "a" * 600
    assert rest == ["b" * 600 + "\n" + "c" * 10]


def test_split_caption_cuts_an_overlong_line():
    caption, rest = split_caption(["x" * 1500], 1024, 4096)
    assert caption == "x" * 1024 and rest == ["x" * 476]


@pytest.mark.parametrize("with_cover", [True, False])
def test_send_reel_sends_the_cover_when_present(tmp_path, monkeypatch, with_cover):
    reel = tmp_path / "demo"
    reel.mkdir()
    (reel / "reel.mp4").write_bytes(b"video")
    (reel / "cover_debug.png").write_bytes(b"debug")  # never sent
    if with_cover:
        (reel / "cover.png").write_bytes(b"png")
    notifier = recording_notifier()
    monkeypatch.setattr(send_to_phone, "probe_video", lambda mp4: {"width": 1080, "height": 1920, "duration": 40.0})
    send_to_phone.send_reel(reel, notifier=notifier)
    calls = notifier.bot.calls
    assert calls[0][0] == "sendVideo"
    photos = [c for c in calls if c[0] == "sendPhoto"]
    assert photos == ([("sendPhoto", {"caption": "demo cover"}, {"photo": "cover.png"})] if with_cover else [])


# ---- The preview of a reel over the upload limit -----------------------------------------------


def test_preview_bitrate_from_the_duration_capped():
    from send_to_phone import PREVIEW, preview_video_kbps

    assert preview_video_kbps(57.7, 317) == PREVIEW["max_video_kbps"]  # reel #44's minute: the cap
    kbps = preview_video_kbps(90.0, 317)
    assert kbps < PREVIEW["max_video_kbps"]
    assert (kbps + 317) * 90.0 * 1000 / 8 <= PREVIEW["target_bytes"] * (1 - PREVIEW["container_share"]) + 1000
    assert preview_video_kbps(3600.0, 317) == PREVIEW["min_video_kbps"]
    assert preview_video_kbps(90.0, None) > kbps  # no audio: all of it for the video


def big_reel(tmp_path, monkeypatch, size=200):
    monkeypatch.setitem(send_to_phone.TELEGRAM, "max_video_bytes", 100)
    mp4 = tmp_path / "reel.mp4"
    mp4.write_bytes(b"v" * size)
    builds = []

    def fake_build(src, preview, duration):
        builds.append((src, preview, duration))
        preview.write_bytes(b"p" * 50)

    monkeypatch.setattr(send_to_phone, "build_preview", fake_build)
    return mp4, builds


def test_a_reel_within_the_limit_goes_as_it_is(tmp_path, monkeypatch):
    mp4, builds = big_reel(tmp_path, monkeypatch, size=100)
    assert send_to_phone.upload_file(mp4, 40.0) == mp4
    assert builds == [] and not (tmp_path / "reel_preview.mp4").exists()


def test_a_reel_over_the_limit_goes_as_its_preview_reused_until_the_reel_is_newer(tmp_path, monkeypatch):
    mp4, builds = big_reel(tmp_path, monkeypatch)
    preview = tmp_path / "reel_preview.mp4"
    assert send_to_phone.upload_file(mp4, 57.7) == preview
    assert builds == [(mp4, preview, 57.7)]
    assert send_to_phone.upload_file(mp4, 57.7) == preview and len(builds) == 1  # cached
    later = preview.stat().st_mtime + 10
    os.utime(mp4, (later, later))  # re-rendered
    assert send_to_phone.upload_file(mp4, 57.7) == preview and len(builds) == 2
    assert mp4.read_bytes() == b"v" * 200  # never touched


def test_a_preview_still_over_the_limit_fails(tmp_path, monkeypatch):
    mp4, _ = big_reel(tmp_path, monkeypatch)
    monkeypatch.setattr(send_to_phone, "build_preview", lambda src, preview, d: preview.write_bytes(b"p" * 150))
    with pytest.raises(SendError, match="reel_preview.mp4 is .* the bot upload limit"):
        send_to_phone.upload_file(mp4, 57.7)


def test_send_reel_uploads_the_preview_of_a_big_reel(tmp_path, monkeypatch):
    reel = tmp_path / "demo"
    reel.mkdir()
    mp4, _ = big_reel(reel, monkeypatch)
    uploaded = []
    notifier = recording_notifier()
    record = notifier.bot.call

    def call(method, payload, files=None, timeout=None):
        if method == "sendVideo":
            uploaded.append(Path(files["video"][1].name).name)
        return record(method, payload, files, timeout)

    notifier.bot.call = call
    monkeypatch.setattr(send_to_phone, "get_notifier", lambda given=None: notifier)
    monkeypatch.setattr(send_to_phone, "probe_video", lambda mp4: {"width": 1080, "height": 1920, "duration": 57.7})
    send_to_phone.send_reel(reel)
    assert uploaded == ["reel_preview.mp4"]
    assert notifier.bot.calls[0][2] == {"video": "demo.mp4"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_preview_is_h264_same_size_audio_copied_faststart_under_the_limit(tmp_path, monkeypatch):
    mp4 = tmp_path / "reel.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1080x1920:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2:sample_rate=48000",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-b:v",
            "20M",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(mp4),
        ],
        check=True,
    )
    monkeypatch.setitem(send_to_phone.TELEGRAM, "max_video_bytes", 1_200_000)
    monkeypatch.setitem(send_to_phone.PREVIEW, "target_bytes", 900_000)
    assert mp4.stat().st_size > 1_200_000
    preview = send_to_phone.upload_file(mp4, 2.0)
    assert preview == tmp_path / "reel_preview.mp4" and preview.stat().st_size <= 1_200_000
    assert not (tmp_path / "reel_preview.part.mp4").exists()

    def streams(path):
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,pix_fmt,bit_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return {s["codec_type"]: s for s in json.loads(out)["streams"]}

    src, out = streams(mp4), streams(preview)
    assert (out["video"]["codec_name"], out["video"]["width"], out["video"]["height"], out["video"]["pix_fmt"]) == (
        "h264",
        1080,
        1920,
        "yuv420p",
    )
    assert out["audio"]["codec_name"] == "aac" and out["audio"]["bit_rate"] == src["audio"]["bit_rate"]
    data = preview.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")
