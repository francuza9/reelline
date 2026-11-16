"""The golden end-to-end test: the example channel's whole chain on fakes (tests/golden/driver.py): a fixture
RSS item fetched through the real fetcher, assessed, picked, briefed, written, styled, fact-checked, its end
card, fact graphics and caption made, the script saved, the voice asked from the TTS fake, a Remotion cover
and Reel frame rendered through the placeholder host, a low-res video made, the review approved and the reel
published to a local folder. FakeLLM / FakeTTS answer from canned text (tests/golden/replies.py); no network.

It runs in a subprocess so the example channel's settings are the only ones any module sees (the other tests
run the first channel's). The render is the cheap mode: stills of the real composition (a full render needs lip sync,
which only Georgian has: language.lipsync_align). Needs node and `npm ci` in engine/remotion."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image
from reelline.paths import REPO_ROOT

DRIVER = Path(__file__).resolve().parent / "golden" / "driver.py"


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    work = tmp_path_factory.mktemp("golden")
    run = subprocess.run(
        [sys.executable, str(DRIVER), str(REPO_ROOT), str(work)], capture_output=True, text=True, timeout=600
    )
    assert run.returncode == 0, run.stderr[-3000:] + run.stdout[-2000:]
    data = json.loads((work / "report.json").read_text(encoding="utf-8"))
    data["work"] = work
    return data


def test_the_chain_runs_to_the_script_in_the_documented_order(report):
    assert report["chain"] == {"status": "ok", "stopped_at": None, "detail": None}
    # No proofread (off), no flirt, straight style: one story pass, one style pass, three fact-checks.
    assert report["purposes"] == [
        "assess",
        "brief",
        "story",
        "punchup_straight",
        "factcheck",
        "endcard",
        "endcard_factcheck",
        "fx_factcheck",
        "caption",
    ]
    assert report["passes"][:2] == ["first/story", "first/punchup"]


def test_ingest_stored_the_fixture_item_and_the_second_feed_failing_did_not_stop_it(report):
    assert report["story"]["source_id"] == "nasa_releases"
    assert report["story"]["title"] == "Rover finds rock layers that formed in an ancient lake"
    assert report["story"]["word_count"] > 80
    assert report["sources"] == {"nasa_releases": 0, "noaa_news": 1}


def test_every_step_left_its_artifact(report):
    rows = report["rows"]
    assert rows["stories"] == rows["assessments"] == rows["picks"] == rows["briefs"] == rows["scripts"] == 1
    assert rows["captions"] == rows["reels"] == rows["reviews"] == 1
    assert "Perseverance" in report["brief"] and "20 rock layers" in report["brief"]
    script = Path(report["script_file"]).read_text(encoding="utf-8")
    assert script.startswith("@cover: Mars had a lake") and script.rstrip().endswith("That's the news.")
    assert report["endcard"] == {"name": "Perseverance rock layers", "what": "20 layers from an ancient Mars lake"}
    assert report["caption"]["hashtags"] and "#example" in report["caption"]["hashtags"]
    assert report["headline"] == ["Mars had a lake", "@cover"]


def test_the_voice_is_one_tts_request_for_the_whole_script(report):
    assert report["tts"]["requests"] == 1 and report["tts"]["words"] > 60 and report["tts"]["bytes"] > 0


def test_the_render_drew_the_placeholder_host_in_the_example_background(report):
    assert report["render"]["background"] == "studio" and report["render"]["mood"] == "happy"
    for key in ("cover", "still"):
        with Image.open(report["render"][key]) as image:
            assert image.size == (1080, 1920)
    with Image.open(report["render"]["cover"]) as cover:
        assert len({cover.convert("RGB").getpixel((x, 1300)) for x in range(300, 800, 25)}) > 1  # not a blank frame


def test_the_review_was_automatic_and_the_reel_went_to_the_local_folder(report):
    assert report["review"]["status"] == "approved" and report["review"]["decided_by"] == "auto"
    assert [(p["channel"], p["state"]) for p in report["publications"]] == [("local", "published")]
    assert report["published"] == ["local/reel.cover.png", "local/reel.mp4", "local/reel.mp4.json"]
    assert "Mars once had a lake" in report["sidecar"]["caption"]
    assert "https://www.nasa.gov/news-release/rover-finds-lake-layers" in report["sidecar"]["caption"]
