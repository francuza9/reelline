"""images.json entries, loading and summaries, attributions.txt."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from reelline.media.images import framing
from reelline.media.images.config import Screen
from reelline.media.images.model import MANIFEST_VERSION, Evaluation, ImageError
from reelline.media.script_markup import Script, image_slots

# --- framing and the manifest ----------------------------------------------------------


def frame(ev: Evaluation, scr: Screen) -> dict:
    """mode, crop, focal and panel color for a chosen image."""
    record = ev.record
    if record["is_logo_or_graphic"] and record["source"] != "generated":
        with Image.open(ev.path) as im:
            luminance = framing.mean_luminance(im)
        return {
            "mode": "contain",
            "crop": None,
            "focal": None,
            "panel_color": framing.panel_color(luminance),
            "luminance": round(luminance, 3),
        }
    crop, focal = framing.cover_crop(record["width"], record["height"], ev.result.get("subject_bbox"), scr)
    return {"mode": "cover", "crop": crop, "focal": focal, "panel_color": None}


def image_entry(n: int, value: str, ev: Evaluation, scr: Screen, excluded: list[str], pinned: bool) -> dict:
    r = ev.record
    return {
        "paragraph": n,
        "value": value,
        "status": "image",
        "pinned": pinned,
        "image_id": r["id"],
        "file": r["path"],
        "width": r["width"],
        "height": r["height"],
        **frame(ev, scr),
        "source": r["source"],
        "license": r["license"],
        "license_url": r["license_url"],
        "author": r["author"],
        "attribution": r["attribution"],
        "attribution_required": bool(r["attribution_required"]),
        "editorial_only": bool(r["editorial_only"]),
        "page_url": r["page_url"],
        "relevance": ev.relevance,
        "vision_reason": ev.result["reason"],
        "excluded": excluded,
    }


def load_manifest(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ImageError(f"{path} is not valid JSON ({exc}); delete it or use --refresh-images") from exc
    if manifest.get("version") != MANIFEST_VERSION:
        return None
    return manifest


def summary_line(entry: dict) -> str:
    n = f"{entry['paragraph']}.{entry['slot']}" if entry.get("slot") else entry["paragraph"]
    if entry["status"] == "image":
        pin = " pinned" if entry.get("pinned") else ""
        cached = " (cached)" if entry.get("cached") else ""
        return (
            f"  ¶{n!s:<4} {entry['source']}, {entry['license']}, relevance {entry['relevance']}, "
            f"{entry['mode']}{pin}: {entry['image_id']} {entry['value']!r}{cached}"
        )
    if entry["status"] == "none":
        return f"  ¶{n!s:<4} none" + ("" if entry["value"] else " (no @image line)")
    return f"  ¶{n!s:<4} no image for {entry['value']!r}: {entry['reason']}" + (
        " (cached)" if entry.get("cached") else ""
    )


def slot_texts(script: Script) -> dict[tuple[int, int], str]:
    """The display text spoken while each picture slot is on screen ((paragraph, slot) ->
    the words from its anchor to the next picture's anchor, or the paragraph's end); the
    paragraph's whole text for a paragraph with one picture."""
    refs = image_slots(script)
    out = {}
    for i, ref in enumerate(refs):
        para = script.paragraphs[ref.paragraph - 1]
        if not para.more_images:
            out[ref.paragraph, ref.slot] = para.display
            continue
        nxt = refs[i + 1] if i + 1 < len(refs) and refs[i + 1].paragraph == ref.paragraph else None
        stop = nxt.token if nxt else len(script.tokens)
        out[ref.paragraph, ref.slot] = " ".join(
            t.text for t in script.tokens[ref.token : stop] if t.paragraph == ref.paragraph - 1
        )
    return out


def attribution_lines(manifest: dict) -> list[str]:
    """One line per distinct image that needs attribution, in paragraph order."""
    lines, seen = [], set()
    for e in manifest["paragraphs"]:
        if e["status"] == "image" and e["attribution_required"] and e["image_id"] not in seen:
            seen.add(e["image_id"])
            lines.append(e["attribution"])
    return lines


def write_outputs(reel_dir: Path, manifest: dict) -> None:
    reel_dir.mkdir(parents=True, exist_ok=True)
    stored = {**manifest, "paragraphs": [{k: v for k, v in e.items() if k != "cached"} for e in manifest["paragraphs"]]}
    (reel_dir / "images.json").write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = attribution_lines(manifest)
    (reel_dir / "attributions.txt").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
