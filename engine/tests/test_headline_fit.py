"""Headline fit (remotion/src/fit.ts), run under Node with a fake text measure.

fit.ts is pure TypeScript without imports, so Node runs it directly (type stripping).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FIT_TS = Path(__file__).resolve().parent.parent / "remotion" / "src" / "fit.ts"
COVER_TSX = FIT_TS.parent / "Cover.tsx"

# Font sizes as in Cover.tsx COVER.headline (test_spec_matches_cover); stroke 0 so widths are exact.
SPEC = {"maxFontPx": 140, "minFontPx": 80, "stepPx": 4, "maxLines": 3, "lineHeight": 1.1, "strokeEm": 0}
BOX = {"width": 960, "height": 660}

# Every character is 0.5 em wide.
DRIVER = """
import {fitHeadline, fitHeadlineOrThrow} from %s;
const [units, box, spec, orThrow] = JSON.parse(process.argv[1]);
const measure = (text, px) => text.length * px * 0.5;
const fit = orThrow ? fitHeadlineOrThrow(units, box, spec, measure, 'Cover headline') : fitHeadline(units, box, spec, measure);
console.log(JSON.stringify(fit));
"""


def run_fit(units, box=BOX, spec=SPEC, or_throw=False):
    node = shutil.which("node")
    if node is None:
        pytest.fail("node not on PATH; it's needed for Remotion anyway")
    result = subprocess.run(
        [
            node,
            "--disable-warning=MODULE_TYPELESS_PACKAGE_JSON",
            "--input-type=module",
            "-e",
            DRIVER % json.dumps(FIT_TS.as_uri()),
            json.dumps([units, box, spec, or_throw]),
        ],
        capture_output=True,
        text=True,
    )
    return result


def fit(units, **kwargs):
    result = run_fit(units, **kwargs)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_short_headline_fits_at_the_largest_size():
    # 11 chars at 140 px = 770 px: one line.
    assert fit(["ახალი", "კურსი"]) == {"fontPx": 140, "lines": ["ახალი კურსი"]}


def test_long_headline_wraps_then_shrinks():
    units = ["უფასო", "AI", "აკადემია", "თბილისში", "ოცდაათი", "ადგილით"]
    result = fit(units)
    # At 140 px a line holds 13 chars: this needs 4 lines, so it shrinks.
    assert SPEC["minFontPx"] <= result["fontPx"] < 140
    assert len(result["lines"]) <= 3
    assert " ".join(result["lines"]) == " ".join(units)
    per_line = BOX["width"] / (result["fontPx"] * 0.5)
    assert all(len(line) <= per_line for line in result["lines"])
    # The next size up doesn't fit.
    bigger = fit(units, spec={**SPEC, "maxFontPx": result["fontPx"] + 4, "minFontPx": result["fontPx"] + 4})
    assert bigger is None


def test_wrap_units_stay_whole():
    result = fit(["OpenAI-ის", "მოდელებმა", "Hugging Face", "გატეხეს"])
    assert "Hugging Face" in result["lines"] or any("Hugging Face" in line for line in result["lines"])


def test_box_height_limits_the_lines():
    # Three lines at 140 px need 462 px; a 400 px box forces a smaller size.
    units = ["აააააააააააა", "ბბბბბბბბბბბბ", "გგგგგგგგგგგგ"]
    assert fit(units)["fontPx"] == 140
    assert fit(units, box={"width": 960, "height": 400})["fontPx"] < 140


def test_too_long_headline_fails():
    units = ["სიტყვა"] * 30
    assert fit(units) is None
    result = run_fit(units, or_throw=True)
    assert result.returncode != 0
    assert "Cover headline" in result.stderr and "doesn't fit 960x660px in 3 lines at 80px" in result.stderr


def test_single_unit_too_wide_fails():
    # 25 chars at 80 px = 1000 px > 960.
    assert fit(["ა" * 25]) is None
    assert fit(["ა" * 24])["fontPx"] == 80


def test_four_lines_at_96_fit_three_below_it():
    # Job #66's shape: every word fits alone, but no two neighbours share a line at
    # 96 px (21 chars = 1008 px), so 3 lines needed a size under the old 96 px floor.
    units = ["ა" * 12, "ბ" * 11, "გ" * 9, "დ" * 15]
    assert fit(units, spec={**SPEC, "minFontPx": 96}) is None
    assert fit(units) == {"fontPx": 88, "lines": ["ა" * 12, f"{'ბ' * 11} {'გ' * 9}", "დ" * 15]}


def test_spec_matches_cover():
    source = COVER_TSX.read_text(encoding="utf-8")
    block = re.search(r"headline: \{(.*?)\n\t\},", source, re.S).group(1)
    for key in ("maxFontPx", "minFontPx", "stepPx", "maxLines", "lineHeight"):
        assert float(re.search(rf"\b{key}: ([\d.]+),", block).group(1)) == SPEC[key], key
