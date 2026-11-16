"""Render the end card (remotion/src/EndCard.tsx) as it holds, for sample facts, and a contact sheet.

Usage: python scripts/render_endcard_preview.py [--sample NAME ...] [--out DIR] [--debug]

Local, on the laptop: no TTS, no model calls, nothing from the database. The EndCard
still (remotion/src/Root.tsx) shows the card settled, its facts from SAMPLES below (each
checked first with reelline/media/outro.py card_problems(), the render's own check): a full card,
one with fields missing, and one with every line at or near its maxChars (the layout's
worst case; it must fit, or the still fails). --debug draws the facts' box.

Outputs in --out (default out/endcard_preview/): card_<N>-<sample>.png, and
card_samples.png, the sheet of the stills rendered in this run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from reelline.paths import REPO_ROOT, SCRIPTS_DIR

sys.path.insert(0, str(SCRIPTS_DIR))

from reelline.media.outro import card_problems, load_outro, ordered
from render_fx_preview import ASSETS, FONTS, PreviewError, contact_sheet, npx_remotion

OUT_DIR = REPO_ROOT / "out" / "endcard_preview"
COMPOSITION = "EndCard"
SHEET = {"columns": 3, "scale": 0.4}
SAMPLES = {
    "full": {
        "name": "უფასო AI აკადემია",
        "what": "3 თვე ისწავლი, როგორ ააწყო AI პროდუქტი",
        "who": "18-29 წლის სტუდენტები",
        "deadline": "15 ნოემბრამდე",
        "cost": "უფასო",
        "apply": "edu.aris.ge",
    },
    "missing": {
        "name": "თსუ-ს მონაცემთა დაცვის კურსი",
        "what": "ონლაინ კურსი და ოფიცრის სერტიფიკატი",
        "deadline": "29 ოქტომბრამდე",
        "cost": "420 ლარი",
    },
    "long": {
        "name": "საზოგადოებრივი უსაფრთხოების ბაკალავრიატი",
        "what": "3 წელი ისწავლი პოლიციის საქმეს: თეორიასაც და პრაქტიკასაც",
        "who": "2027-ში ერთიანი ეროვნული გამოცდების აბიტურიენტები",
        "deadline": "15 თებერვლამდე, 18:00 საათამდე",
        "cost": "სრულად დაფინანსებული სწავლა",
        "apply": "scholarships.rustaveli.org.ge",
    },
}


def check_samples(names: list[str]) -> None:
    outro = load_outro()
    for name in names:
        problems = card_problems(SAMPLES[name], outro)
        if problems:
            raise PreviewError(f"sample {name}: " + "; ".join(problems))


def still_path(out_dir: Path, name: str) -> Path:
    return out_dir / f"card_{list(SAMPLES).index(name) + 1}-{name}.png"


def stage_public_dir(public_dir: Path) -> None:
    (public_dir / "fonts").mkdir(parents=True)
    for font in FONTS:
        shutil.copy2(ASSETS / "fonts" / font, public_dir / "fonts" / font)


def render(names: list[str], out_dir: Path, debug: bool = False) -> list[Path]:
    check_samples(names)
    outro = load_outro()
    out_dir = out_dir.resolve()  # Remotion runs in remotion/
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="endcard_preview_") as tmp:
        public_dir, bundle = Path(tmp) / "public", Path(tmp) / "bundle"
        stage_public_dir(public_dir)
        print("Bundling ...", flush=True)
        npx_remotion(["bundle", f"--public-dir={public_dir}", f"--out-dir={bundle}", "--log=error"])
        for name in names:
            out = still_path(out_dir, name)
            props = json.dumps({"facts": ordered(SAMPLES[name], outro), "debugSafeArea": debug}, ensure_ascii=False)
            npx_remotion(["still", str(bundle), COMPOSITION, str(out), f"--props={props}", "--log=error"])
            print(f"  {out}", flush=True)
            written.append(out)
    sheet = out_dir / "card_samples.png"
    contact_sheet(
        written,
        [f"{list(SAMPLES).index(n) + 1}  {n}" for n in names],
        "End card samples",
        sheet,
        SHEET["columns"],
        SHEET["scale"],
    )
    print(f"  {sheet}", flush=True)
    return written + [sheet]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="append", choices=list(SAMPLES), help="render only these (repeatable)")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--debug", action="store_true", help="draw the facts' box")
    args = parser.parse_args()
    try:
        render(args.sample or list(SAMPLES), args.out, args.debug)
    except PreviewError as exc:
        sys.exit(f"FAIL: {exc}")


if __name__ == "__main__":
    main()
