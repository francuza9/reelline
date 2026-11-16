"""Wall-TV image library and search tool.

Usage:
  python scripts/images.py search "<query>" --text "<paragraph text>" [--card "..."]
      Dry run of the picker for one paragraph: prints every candidate with source,
      license, pass/fail and reasons, and writes a contact sheet to
      out/image_search/<slug>.png. No library rows are written (downloaded files and
      vision results are still cached).
  python scripts/images.py add <file> --source-url <url> --license "<license>" --author "<name>"
                               [--tags a,b] [--editorial-only]
      Manual add (official logos, press-kit images). Same checks as search except
      relevance. A license outside the allowlist (CC0, PD, CC BY, CC BY-SA) is
      accepted only with --editorial-only (press-kit terms).
  python scripts/images.py list [--tag x] [--all]
  python scripts/images.py show <id>
  python scripts/images.py reject <id>
  python scripts/images.py compare <reel name> [<reel name> ...] --library <copy> [--root DIR] [--db PATH]
                                   [--out DIR] --max-cost-usd 0.15
      Re-resolve published reels' @image lines with the current picker into --out
      (<reel>/images.json, never the reel's own), against a copy of the image library
      (the picker writes into it), with the cooldown as it stood before each reel; one
      contact sheet per reel, <out>/<reel>.png: each paragraph's text, the OLD image (its
      images.json) and the NEW one, as the TV shows them. Paid calls stop at the cap and
      are recorded as manual.

See channel docs/images.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reelline.config import conf
from reelline.config.credentials import ConfigError
from reelline.media.images.config import CONTACT_SHEET, GENERATED, SEARCH_OUT_DIR, Screen, screen, screen_from_spec
from reelline.media.images.cooldown import recently_shown
from reelline.media.images.framing import resolution_problem
from reelline.media.images.library import Library, image_id
from reelline.media.images.licenses import attribution_line, classify_license_name
from reelline.media.images.query import parse_query
from reelline.media.images.resolve import Evaluation, Picker, generation_seed, resolve_reel
from reelline.media.images.sources import SourceError, open_image
from reelline.media.images.vision import VisionError, check, facts_problems
from reelline.media.script_markup import MarkupError, parse, parse_image
from reelline.paths import REPO_ROOT
from reelline.pipeline.db import manual_recording, record_manual
from reelline.providers import connection


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "query"


def contact_sheet(evals: list[Evaluation], title: str, out: Path) -> Path | None:
    """Grid of the candidates that have a file, labelled with the verdict."""
    cs = CONTACT_SHEET
    shown = [e for e in evals if e.path is not None]
    if not shown:
        return None
    cols = min(cs["columns"], len(shown))
    rows = -(-len(shown) // cols)
    cell_w, cell_h = cs["thumb_w"], cs["thumb_h"] + cs["label_h"]
    header = cs["font_px"] * 2
    sheet = Image.new(
        "RGB",
        (cols * cell_w + (cols + 1) * cs["gap"], header + rows * cell_h + (rows + 1) * cs["gap"]),
        cs["background"],
    )
    draw = ImageDraw.Draw(sheet)
    if Path(cs["font"]).is_file():
        font = ImageFont.truetype(cs["font"], cs["font_px"])
    else:
        print(f"NOTE: contact sheet font {cs['font']} not found; using Pillow's default (no Georgian glyphs)")
        font = ImageFont.load_default(cs["font_px"])
    draw.text((cs["gap"], cs["gap"] // 2), title, fill=cs["text"], font=font)
    chars = cs["thumb_w"] // (cs["font_px"] // 2 + 1)
    for i, ev in enumerate(shown):
        x = cs["gap"] + (i % cols) * (cell_w + cs["gap"])
        y = header + cs["gap"] + (i // cols) * (cell_h + cs["gap"])
        with Image.open(ev.path) as im:
            thumb = im.convert("RGBA")
        thumb.thumbnail((cs["thumb_w"], cs["thumb_h"]))
        backdrop = Image.new("RGBA", thumb.size, (128, 128, 128, 255))
        sheet.paste(
            Image.alpha_composite(backdrop, thumb).convert("RGB"),
            (x + (cs["thumb_w"] - thumb.width) // 2, y + (cs["thumb_h"] - thumb.height) // 2),
        )
        color = cs["pass"] if ev.status == "pass" else cs["fail"]
        verdict = "PASS" if ev.status == "pass" else "FAIL"
        lines = [
            f"{verdict} {ev.source} | {ev.license} | rel {ev.relevance}",
            *textwrap.wrap("; ".join(ev.reasons) or (ev.result or {}).get("reason", ""), chars)[:3],
        ]
        for k, line in enumerate(lines):
            draw.text(
                (x, y + cs["thumb_h"] + 4 + k * (cs["font_px"] + 4)),
                line,
                fill=color if k == 0 else cs["text"],
                font=font,
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def print_evaluations(evals: list[Evaluation]) -> None:
    for ev in evals:
        mark = {"pass": "PASS", "fail": "FAIL", "rejected": "SKIP"}[ev.status]
        rel = f"rel {ev.relevance:>2}" if ev.relevance is not None else "rel  -"
        cached = " (cached)" if ev.cached else ""
        print(
            f"  {mark} {ev.source:<9} {str(ev.license or '-')[:22]:<22} {rel}  "
            f"{ev.image_id or '-':<12}  {ev.title[:70]}{cached}"
        )
        if ev.reasons:
            print(f"       reasons: {'; '.join(ev.reasons)}")
        elif ev.result:
            print(f"       vision: {ev.result['reason']}")


def cmd_search(args) -> None:
    try:
        parse_image(args.query)
    except MarkupError as exc:
        sys.exit(f"FAIL: {exc}")
    library = Library()
    picker = Picker(library, lambda: connection("vision"), dry_run=True)
    try:
        with manual_recording():
            result = picker.pick(
                args.query, args.text, args.card, generation_seed("search", args.text, args.query), full=True
            )
    finally:
        record_manual(picker.calls)  # dev spend is real spend
    print(f"Candidates for {args.query!r} ({len(result.evaluations)}):")
    print_evaluations(result.evaluations)
    if result.chosen:
        c = result.chosen
        print(f"Pick: {c.image_id} ({c.source}, {c.license}, relevance {c.relevance}) {c.title}")
    else:
        print(f"Pick: none - {result.reason}")
    if result.generated:
        fallback = "tried"
    elif result.chosen:
        fallback = "not needed"
    else:
        fallback = "skipped (logo-type query)" if GENERATED["enabled"] else "disabled"
    print(f"Generated fallback: {fallback}")
    sheet = contact_sheet(
        result.evaluations, f"{args.query} | {args.card or ''}", SEARCH_OUT_DIR / f"{slug(args.query)}.png"
    )
    print(f"Contact sheet: {sheet}" if sheet else "Contact sheet: no downloaded candidates")


def cmd_add(args) -> None:
    library = Library()
    data = args.file.read_bytes()
    try:
        im, ext = open_image(data)
    except SourceError as exc:
        sys.exit(f"FAIL: {args.file}: {exc}")
    problem = resolution_problem(im.width, im.height, screen())
    if problem:
        sys.exit(f"FAIL: {args.file}: {problem}")
    family = classify_license_name(args.license)
    if family is None and not args.editorial_only:
        sys.exit(
            f"FAIL: license {args.license!r} is not in the allowlist (CC0, public domain, CC BY, CC BY-SA); "
            "pass --editorial-only for official/press-kit images"
        )
    sha, path = library.store_file(data, ext)
    existing = library.get(image_id(sha))
    if existing:
        sys.exit(f"FAIL: already in the library as {existing['id']} ({existing['status']})")
    tags = [t.strip().lower() for t in (args.tags or "").split(",") if t.strip()]
    query = " ".join(tags) or args.file.stem.replace("_", " ")
    calls: list[dict] = []
    try:
        with manual_recording():
            result, _ = check(
                connection("vision"), library, sha, path, "(manual add, no paragraph)", None, query, calls
            )
    except VisionError as exc:
        sys.exit(f"FAIL: {exc}")
    finally:
        record_manual(calls)
    intrinsic = facts_problems(result)
    record = library.put(
        {
            "id": image_id(sha),
            "sha256": sha,
            "path": str(path.relative_to(library.root)),
            "width": im.width,
            "height": im.height,
            "mime": Image.MIME.get(im.format),
            "source": "manual",
            "source_key": f"manual:{sha}",
            "page_url": args.source_url,
            "file_url": args.source_url,
            "license": args.license,
            "license_url": None,
            "author": args.author,
            "attribution": attribution_line("manual", args.author, args.license),
            "attribution_required": family not in ("cc0", "pd"),
            "editorial_only": args.editorial_only or bool(result["is_logo_or_graphic"]),
            "queries": [],
            "tags": sorted(set(tags) | set(result["tags"])),
            "vision": result,
            "is_logo_or_graphic": result["is_logo_or_graphic"],
            # What it shows by the adder's word: a named-entity query matches these.
            "subjects": tags,
            "status": "rejected" if intrinsic else "accepted",
            "reason": ", ".join(intrinsic) or None,
        }
    )
    if intrinsic:
        sys.exit(f"FAIL: rejected ({', '.join(intrinsic)}); stored as {record['id']} so it isn't re-added")
    print(
        f"Added {record['id']}: {record['width']}x{record['height']}, "
        f"{'logo/graphic' if record['is_logo_or_graphic'] else 'photo'}, tags {', '.join(record['tags'])}"
    )
    print(f"Pin it with: @image: lib:{record['id']}")


def cmd_list(args) -> None:
    rows = Library().all(status=None if args.all else "accepted", tag=args.tag)
    for r in rows:
        size = f"{r['width']}x{r['height']}" if r["width"] else "-"
        kind = "logo" if r["is_logo_or_graphic"] else ("photo" if r["is_logo_or_graphic"] is False else "-")
        print(
            f"{r['id']}  {r['status']:<8} {r['source']:<9} {str(r['license'] or '-')[:18]:<18} {size:>10} "
            f"{kind:<5} {', '.join((r['tags'] or [])[:4])}"
            + (f"  [{r['reason']}]" if r["status"] == "rejected" else "")
        )
    print(f"{len(rows)} image(s)")


def cmd_show(args) -> None:
    record = Library().get(args.id)
    if record is None:
        sys.exit(f"FAIL: no library image {args.id}")
    print(json.dumps(record, ensure_ascii=False, indent=1))


def cmd_reject(args) -> None:
    library = Library()
    if library.get(args.id) is None:
        sys.exit(f"FAIL: no library image {args.id}")
    library.update(args.id, status="rejected", reason="rejected by hand")
    print(f"Rejected {args.id}")


# --- compare: OLD vs NEW per paragraph -----------------------------------------------------


def _font(px: int):
    if Path(CONTACT_SHEET["font"]).is_file():
        return ImageFont.truetype(CONTACT_SHEET["font"], px)
    return ImageFont.load_default(px)


def tv_thumb(entry: dict | None, library_root: Path, w: int, h: int) -> Image.Image:
    """What the TV shows for a manifest entry, at w x h: the cover crop, the logo on its
    panel, or the idle face (dark, "idle screen")."""
    cs = CONTACT_SHEET
    if not entry or entry.get("status") != "image":
        idle = Image.new("RGB", (w, h), "#0E0D22")
        draw = ImageDraw.Draw(idle)
        label = "idle screen" if entry and entry.get("status") == "no_candidate" else "no image (none)"
        font = _font(cs["font_px"] + 4)
        tw = draw.textlength(label, font=font)
        draw.text(((w - tw) / 2, h / 2 - cs["font_px"]), label, fill="#6E6A99", font=font)
        return idle
    path = library_root / entry["file"]
    if not path.is_file():
        return Image.new("RGB", (w, h), "#552222")
    with Image.open(path) as im:
        im = im.convert("RGBA")
    if entry.get("mode") == "cover" and entry.get("crop"):
        c = entry["crop"]
        box = (c["x"] * im.width, c["y"] * im.height, (c["x"] + c["w"]) * im.width, (c["y"] + c["h"]) * im.height)
        return im.crop(tuple(round(v) for v in box)).convert("RGB").resize((w, h), Image.LANCZOS)
    panel = Image.new("RGBA", (w, h), entry.get("panel_color") or "#1E1C45")
    im.thumbnail((int(w * 0.8), int(h * 0.8)))
    panel.alpha_composite(im, ((w - im.width) // 2, (h - im.height) // 2))
    return panel.convert("RGB")


def entry_label(entry: dict | None) -> list[str]:
    if not entry:
        return ["-"]
    if entry["status"] == "image":
        return [f"{entry['source']} | {entry['license']}", f"{entry['image_id']} | relevance {entry.get('relevance')}"]
    if entry["status"] == "none":
        return ["@image: none"]
    return ["idle screen: " + (entry.get("reason") or "")]


def compare_sheet(script, old: dict | None, new: dict, library_root: Path, title: str, out: Path) -> Path:
    """One row per paragraph with an @image line: ¶, query and text | OLD | NEW."""
    cs = CONTACT_SHEET
    tw_, th_ = 540, 231  # the TV face's 21:9
    text_w, gap, label_h = 470, 16, 3 * (cs["font_px"] + 4) + 8
    row_h = th_ + label_h
    # Each paragraph's first picture only (an extra one has "slot" > 0).
    old_by = {e["paragraph"]: e for e in (old or {}).get("paragraphs", []) if not e.get("slot")}
    new_by = {e["paragraph"]: e for e in new["paragraphs"] if not e.get("slot")}
    rows = [n for n, p in enumerate(script.paragraphs, start=1) if p.image is not None]
    head = 3 * (cs["font_px"] + 8)
    sheet = Image.new("RGB", (text_w + 2 * tw_ + 4 * gap, head + len(rows) * (row_h + gap) + gap), cs["background"])
    draw = ImageDraw.Draw(sheet)
    font, big = _font(cs["font_px"]), _font(cs["font_px"] + 4)
    draw.text((gap, gap // 2), title, fill=cs["text"], font=big)
    x_old, x_new = text_w + 2 * gap, text_w + tw_ + 3 * gap
    draw.text((x_old, head - cs["font_px"] - 8), "OLD (the published reel)", fill=cs["text"], font=big)
    draw.text((x_new, head - cs["font_px"] - 8), "NEW", fill=cs["pass"], font=big)
    chars = text_w // (cs["font_px"] // 2 + 1)
    for k, n in enumerate(rows):
        para = script.paragraphs[n - 1]
        y = head + gap + k * (row_h + gap)
        q = parse_query(para.image.value) if para.image.kind == "query" else None
        kind = (f"named: {', '.join(q.entities)}" + (" (logo)" if q.logo else "")) if q and q.named else "generic"
        lines = [(f"¶{n}  {para.image.raw}", cs["pass"]), (kind if q else para.image.kind, cs["text"]), ("", None)]
        lines += [(line, cs["text"]) for line in textwrap.wrap(para.display, chars)[:9]]
        for i, (line, color) in enumerate(lines):
            if line:
                draw.text((gap, y + i * (cs["font_px"] + 4)), line, fill=color, font=font)
        for x, entry in ((x_old, old_by.get(n)), (x_new, new_by.get(n))):
            sheet.paste(tv_thumb(entry, library_root, tw_, th_), (x, y))
            label = entry_label(entry)
            wrapped = [w for line in label for w in textwrap.wrap(line, tw_ // (cs["font_px"] // 2 + 1))][:3]
            color = cs["pass"] if entry and entry["status"] == "image" else cs["fail"]
            for i, line in enumerate(wrapped):
                draw.text(
                    (x, y + th_ + 4 + i * (cs["font_px"] + 4)), line, fill=color if i == 0 else cs["text"], font=font
                )
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def reel_screen(background: str | None) -> Screen:
    """The TV of the reel's background (screen.json without one)."""
    if not background:
        return screen()
    from reelline.media.backgrounds import BackgroundError, get_background, screen_spec

    try:
        return screen_from_spec(screen_spec(get_background(Path(background).stem)))
    except BackgroundError:
        return screen()


def cmd_compare(args) -> None:
    root = args.root.resolve()
    db_path = args.db or conf().storage.db_path
    library = Library(args.library.resolve())
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    picker = Picker(library, lambda: connection("vision"), cost_cap_usd=args.max_cost_usd)
    sheets = []
    for name in args.reels:
        reel = conn.execute(
            """SELECT r.id, r.created_at, r.background, r.images_path, s.id AS script_id, s.path
                               FROM reels r JOIN scripts s ON s.id = r.script_id
                               WHERE r.name = ? AND r.images_path IS NOT NULL ORDER BY r.id DESC LIMIT 1""",
            (name,),
        ).fetchone()
        if reel is None:
            sys.exit(f"FAIL: no rendered reel named {name}")
        script = parse((root / reel["path"]).read_text(encoding="utf-8"))
        old_path = root / reel["images_path"]
        old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.is_file() else None
        brief = conf().channel.dir / "content" / "briefs" / f"{name}.md"
        cooldown = recently_shown(conn, root, name, before=reel["created_at"])
        print(f"== {name} (reel #{reel['id']}, script {reel['script_id']}): cooldown {len(cooldown)} image(s)")
        picker.screen = reel_screen(reel["background"])
        first = len(picker.calls)
        try:
            with manual_recording(name, db_path=db_path):
                new = resolve_reel(
                    script,
                    args.out / name,
                    lambda: picker,
                    name,
                    refresh=True,
                    cooldown=cooldown,
                    context=brief.read_text(encoding="utf-8") if brief.is_file() else "",
                )
        finally:
            record_manual(picker.calls[first:], script_name=name, db_path=db_path)
        spent = sum(c["cost_usd"] or 0 for c in picker.calls[first:])
        title = (
            f"{name}  (script {reel['script_id']}, reel #{reel['id']})  "
            f"vision ${spent:.4f}, {len(picker.calls) - first} call(s)"
        )
        sheets.append(compare_sheet(script, old, new, library.root, title, args.out / f"{name}.png"))
        print(f"Contact sheet: {sheets[-1]}")
    total = picker.spent_usd()
    print(
        f"Total: {len(picker.calls)} vision call(s), ${total:.4f} (cap ${args.max_cost_usd:.2f})"
        + ("; the cap was reached" if picker.capped else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("search", help="dry-run the picker for one paragraph")
    p.add_argument("query")
    p.add_argument("--text", required=True, help="the paragraph's display text")
    p.add_argument("--card", default=None, help="the paragraph's @card headline")
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("add", help="add an official/press-kit image by hand")
    p.add_argument("file", type=Path)
    p.add_argument("--source-url", required=True)
    p.add_argument("--license", required=True)
    p.add_argument("--author", required=True)
    p.add_argument("--tags", default=None, help="comma-separated tags")
    p.add_argument("--editorial-only", action="store_true")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("list", help="list accepted images")
    p.add_argument("--tag", default=None)
    p.add_argument("--all", action="store_true", help="include rejected candidates")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("show", help="print one image's record")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)
    p = sub.add_parser("reject", help="mark an image rejected")
    p.add_argument("id")
    p.set_defaults(func=cmd_reject)
    p = sub.add_parser("compare", help="re-resolve reels' images against a library copy; OLD vs NEW sheets")
    p.add_argument("reels", nargs="+", metavar="reel")
    p.add_argument("--library", type=Path, required=True, help="a copy of data/image_library (written to)")
    p.add_argument("--root", type=Path, default=REPO_ROOT, help="the checkout the reels and scripts are in")
    p.add_argument("--db", type=Path, default=None, help="the database (default: storage.db_path)")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "out" / "images_compare")
    p.add_argument("--max-cost-usd", type=float, required=True, help="no vision call past this spend")
    p.set_defaults(func=cmd_compare)
    args = parser.parse_args()
    try:
        args.func(args)
    except (ConfigError, VisionError, SourceError) as exc:
        sys.exit(f"FAIL: {exc}")


if __name__ == "__main__":
    main()
