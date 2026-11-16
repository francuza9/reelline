"""Render the fx design preview: each fact-graphic sample popped out of the TV (or on it).

Usage: python scripts/render_fx_preview.py [--sample NAME ...] [--placement popout|tv ...]
                                           [--no-video]

Local, on the laptop: no TTS, no model calls, no audio, nothing from the database. The
FxPreview composition (remotion/src/FxPreview.tsx) shows one sample (a template with
sample values, remotion/src/fx/) in one placement in the real scene: the night
background, the wall TV on its idle face, the host idling on his chair with the rest mouth,
and a sample caption line. The samples, the timing and every look tunable are in
remotion/src/fx.json (preview.samples: one per template, plus format's onsite variant and
two more abroad destinations).

Placements: B (popout, the default and the chosen one) jumps out of the TV, plays big in
front of the scene above the caption line, then goes back in; A (tv, kept but no longer
previewed unless asked) plays inside the TV's face.

Outputs in out/fx_preview_v3/ (emptied first when everything is rendered), per
placement label (B-popout, A-tv) and sample, numbered in fx.json order:
  <label>_<NN>-<sample>_1-start.png     just after the fx starts (B: the card jumping
                                        out, A: the face switching on)
  <label>_<NN>-<sample>_2-mid.png       mid-animation
  <label>_<NN>-<sample>_3-settled.png   settled, before it leaves
  <label>_<NN>-<sample>.mp4             the whole preview, no audio
  <label>_settled-<K>.png               the settled stills, labelled, SHEETS["settled"]
                                        per_sheet to a sheet (<label>_settled.png when
                                        one sheet holds them all)
  <label>_mid.png                       the mid stills, cropped to the card, labelled
The sheets hold the samples rendered in this run (--sample picks them).

Steps: stage a Remotion public dir (copies: Remotion's server 404s symlinks) and bundle
once into a temp dir, then `npx remotion still` / `render` from that bundle, the video at
CONCURRENCY frames in parallel (as on the server), then the contact sheets (Pillow).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reelline.config import conf
from reelline.paths import REPO_ROOT
from reelline.render.registry import get_rig
from reelline.render.remotion import run_cli

ASSETS = conf().channel.dir / "assets"
FX_JSON = conf().channel.graphics_dir / "fx.json"
OUT_DIR = REPO_ROOT / "out" / "fx_preview_v3"

COMPOSITION = "FxPreview"
# Fonts the captions and fx load from the public dir (remotion/src/Captions.tsx).
FONTS = tuple(conf().channel.fonts)
# Frames rendered in parallel (the server's render.remotion_concurrency).
CONCURRENCY = 2

PLACEMENTS = {"popout": "B-popout", "tv": "A-tv"}
DEFAULT_PLACEMENTS = ["popout"]
TITLES = {"popout": "B: popped out", "tv": "A: on the wall TV"}

# Contact sheets: settled stills whole (the composition), per_sheet to a sheet; mid
# stills cropped to `crop` (left, top, right, bottom in frame pixels: the card and the
# caption), all on one sheet.
SHEETS = {
    "settled": {"columns": 4, "scale": 0.4, "per_sheet": 8, "crop": None},
    "mid": {"columns": 4, "scale": 0.45, "per_sheet": None, "crop": (0, 140, 1080, 1000)},
}
CONTACT = {
    "gap_px": 24,
    "title_px": 96,
    "label_px": 60,
    "title_size": 52,
    "label_size": 38,
    "background": "#14120C",
    "text": "#FFFFFF",
    "accent": "#FDDB0A",
    "font": ASSETS / "fonts" / "Fredoka-Variable.ttf",
    "font_weight": 600,
}


class PreviewError(RuntimeError):
    """A failed staging, render or contact sheet (the message says which)."""


def load_spec() -> dict:
    return json.loads(FX_JSON.read_text(encoding="utf-8"))


def samples(spec: dict) -> list[str]:
    return list(spec["preview"]["samples"])


def template_of(spec: dict, sample: str) -> str:
    return spec["preview"]["samples"][sample]["data"]["template"]


def still_times(spec: dict, sample: str, placement: str) -> dict[str, float]:
    """Seconds into the preview of the three stills (fx.json preview.stills). The template
    starts leadS after the fx (FxOnTv / FxPopout in remotion/src/Fx.tsx)."""
    preview = spec["preview"]
    stills = preview["stills"]
    start = preview["fxStartS"] + spec["placements"][placement]["leadS"]
    duration = spec["templates"][template_of(spec, sample)]["durationS"]
    return {
        "1-start": preview["fxStartS"] + stills["startAfterS"][placement],
        "2-mid": start + stills["midS"][sample],
        "3-settled": start + duration + stills["settledAfterS"],
    }


def leave_s(spec: dict, placement: str) -> float:
    """When the fx starts leaving: the card flying back, or the TV face switching off."""
    p = spec["placements"][placement]
    out = p["exit"] if placement == "tv" else p["back"]
    return spec["preview"]["fxEndS"] - out["durationS"]


def stem(spec: dict, placement: str, sample: str) -> str:
    return f"{PLACEMENTS[placement]}_{samples(spec).index(sample) + 1:02d}-{sample}"


def still_path(spec: dict, placement: str, sample: str, still: str) -> Path:
    return OUT_DIR / f"{stem(spec, placement, sample)}_{still}.png"


def video_path(spec: dict, placement: str, sample: str) -> Path:
    return OUT_DIR / f"{stem(spec, placement, sample)}.mp4"


def stage_public_dir(public_dir: Path, background: str) -> None:
    (public_dir / "backgrounds").mkdir(parents=True)
    (public_dir / "fonts").mkdir()
    shutil.copy2(ASSETS / "backgrounds" / background, public_dir / "backgrounds" / background)
    get_rig().stage(public_dir)
    for font in FONTS:
        shutil.copy2(ASSETS / "fonts" / font, public_dir / "fonts" / font)


def npx_remotion(args: list[str]) -> None:
    """Run the Remotion CLI in remotion/; a failure raises with the end of its output."""
    result = run_cli(args, capture=True)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-25:])
        raise PreviewError(f"remotion {' '.join(args[:3])} exited with {result.returncode}:\n{tail}")


def load_font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(CONTACT["font"]), size)
    # A FreeType without variable-font support keeps the default weight.
    with contextlib.suppress(OSError, ValueError):
        font.set_variation_by_axes([CONTACT["font_weight"]])
    return font


def contact_sheet(
    stills: list[Path],
    labels: list[str],
    title: str,
    out: Path,
    columns: int,
    scale: float,
    crop: tuple[int, int, int, int] | None = None,
) -> None:
    """The stills (cropped to `crop`, then scaled) in a labelled grid under a title."""
    c = CONTACT
    with Image.open(stills[0]) as first:
        w, h = (crop[2] - crop[0], crop[3] - crop[1]) if crop else first.size
    cell_w, cell_h = round(w * scale), round(h * scale)
    cols = min(columns, len(stills))
    rows = -(-len(stills) // cols)
    gap = c["gap_px"]
    width = cols * cell_w + (cols + 1) * gap
    height = c["title_px"] + rows * (c["label_px"] + cell_h) + (rows + 1) * gap
    sheet = Image.new("RGB", (width, height), c["background"])
    draw = ImageDraw.Draw(sheet)
    draw.text((gap, c["title_px"] / 2 + gap / 2), title, font=load_font(c["title_size"]), fill=c["accent"], anchor="lm")
    label_font = load_font(c["label_size"])
    for i, (path, label) in enumerate(zip(stills, labels, strict=False)):
        row, col = divmod(i, cols)
        x = gap + col * (cell_w + gap)
        y = c["title_px"] + gap + row * (c["label_px"] + cell_h + gap)
        draw.text((x, y + c["label_px"] / 2), label, font=label_font, fill=c["text"], anchor="lm")
        with Image.open(path) as im:
            im = im.convert("RGB")
            if crop:
                im = im.crop(crop)
            sheet.paste(im.resize((cell_w, cell_h), Image.LANCZOS), (x, y + c["label_px"]))
    sheet.save(out)


def contact_sheets(spec: dict, placement: str, names: list[str]) -> list[Path]:
    """The settled sheets and the mid sheet for these samples (none if a still is missing)."""
    order = samples(spec)
    labels = [f"{order.index(name) + 1}  {name.replace('_', ' ')}" for name in names]
    label = PLACEMENTS[placement]
    written: list[Path] = []
    for still, kind in (("3-settled", "settled"), ("2-mid", "mid")):
        paths = [still_path(spec, placement, name, still) for name in names]
        if not all(p.is_file() for p in paths):
            print(f"  (no {kind} sheet for {label}: not every {still} still is there)")
            continue
        cfg = SHEETS[kind]
        per = cfg["per_sheet"] or len(paths)
        chunks = [range(i, min(i + per, len(paths))) for i in range(0, len(paths), per)]
        for k, idx in enumerate(chunks, 1):
            name = f"{label}_{kind}-{k}.png" if len(chunks) > 1 else f"{label}_{kind}.png"
            part = f" ({k}/{len(chunks)})" if len(chunks) > 1 else ""
            out = OUT_DIR / name
            contact_sheet(
                [paths[i] for i in idx],
                [labels[i] for i in idx],
                f"{TITLES[placement]}  -  {kind} frames{part}",
                out,
                cfg["columns"],
                cfg["scale"],
                cfg["crop"],
            )
            written.append(out)
    return written


def render(names: list[str], placements: list[str], video: bool) -> list[Path]:
    spec = load_spec()
    preview = spec["preview"]
    fps = preview["fps"]
    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="fx_preview_") as tmp:
        public_dir, bundle = Path(tmp) / "public", Path(tmp) / "bundle"
        stage_public_dir(public_dir, preview["background"])
        print("Bundling ...", flush=True)
        npx_remotion(["bundle", f"--public-dir={public_dir}", f"--out-dir={bundle}", "--log=error"])
        for placement in placements:
            for name in names:
                props = json.dumps({"sample": name, "placement": placement})
                for still, t in still_times(spec, name, placement).items():
                    out = still_path(spec, placement, name, still)
                    frame = round(t * fps)
                    npx_remotion(
                        [
                            "still",
                            str(bundle),
                            COMPOSITION,
                            str(out),
                            f"--frame={frame}",
                            f"--props={props}",
                            "--log=error",
                        ]
                    )
                    print(f"  {out.relative_to(REPO_ROOT)} (frame {frame}, {t:.2f}s)", flush=True)
                    written.append(out)
                if video:
                    out = video_path(spec, placement, name)
                    npx_remotion(
                        [
                            "render",
                            str(bundle),
                            COMPOSITION,
                            str(out),
                            f"--props={props}",
                            "--muted",
                            f"--concurrency={CONCURRENCY}",
                            "--log=error",
                        ]
                    )
                    print(f"  {out.relative_to(REPO_ROOT)}", flush=True)
                    written.append(out)
    for placement in placements:
        for out in contact_sheets(spec, placement, names):
            print(f"  {out.relative_to(REPO_ROOT)}", flush=True)
            written.append(out)
    return written


def main() -> None:
    spec = load_spec()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sample", action="append", choices=samples(spec), help="only this sample (repeatable; default: all)"
    )
    parser.add_argument(
        "--placement",
        action="append",
        choices=list(PLACEMENTS),
        help=f"only this placement (repeatable; default: {', '.join(DEFAULT_PLACEMENTS)})",
    )
    parser.add_argument("--no-video", action="store_true", help="stills and contact sheets only")
    args = parser.parse_args()
    names = args.sample or samples(spec)
    placements = args.placement or DEFAULT_PLACEMENTS
    everything = not args.sample and not args.placement and not args.no_video
    if everything and OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        written = render(names, placements, video=not args.no_video)
    except PreviewError as exc:
        sys.exit(f"FAIL: {exc}")
    print(f"Wrote {len(written)} files to {OUT_DIR.relative_to(REPO_ROOT)}/")


if __name__ == "__main__":
    main()
