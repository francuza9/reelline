"""Pick a wall-TV image per paragraph and write the reel's image manifest.

Picker.pick() for one query and paragraph checks candidates in the order of
SOURCE_ORDER for the query's type (query.py: generic scene or named entity) and takes
the first that passes the vision check:
  - generic: library matches, then Pixabay, Pexels, Commons;
  - named: library images whose source names the entity, then Commons files that name
    it (title or categories); never a stock photo.
A library image costs one short relevance call (none when cached), at most
CANDIDATES["library_paid_calls"] per paragraph; a search result is downloaded into the
library, checked, and recorded (accepted or rejected). Up to CANDIDATES["per_source"]
from each source. Before any vision call, candidates are filtered on license, file
type, size, aspect (CANDIDATES["min_aspect"]), earlier rejection, duplicate files, the
excluded images (the cooldown among them) and, for a named query, whether the file names
the entity. At most CANDIDATES["max_vision_calls"] paid vision calls per paragraph, and
with a cost cap (cost_cap_usd) none past it; if none passed, the paragraph shows the
idle screen (logged); only with GENERATED["enabled"] (off) is a generated illustration
tried before that. A missing PIXABAY_API_KEY or PEXELS_API_KEY skips that source, and a
failing source is skipped; both are printed.

resolve_reel() does that for every picture slot: each paragraph's first @image and the
extra ones anchored to a word (script_markup.image_slots(); the extra ones get fewer
vision calls, CANDIDATES["extra_slot_max_vision_calls"], from a per-reel budget,
["extra_slots_vision_budget"], and never the generated fallback), reusing the previous images.json
entry when a paragraph's @image value is unchanged, and writes images.json and
attributions.txt (see write_outputs()). A slot doesn't get the previous
slot's image unless both have the same @image value
(CANDIDATES["avoid_adjacent_repeat"]), nor an image already chosen for another value in
the reel (["avoid_reel_repeat"]), nor an image from the cooldown (cooldown.py)
unless it's the logo of an organization the story names; a logo query for an
organization the script and brief don't name is skipped (no picture for the slot: the
renderer keeps the previous one, idle only before the first).
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Set as AbstractSet
from pathlib import Path

from reelline.media.images.config import CANDIDATES, SOURCES
from reelline.media.images.library import Library
from reelline.media.images.manifest import (
    attribution_lines,
    frame,
    image_entry,
    load_manifest,
    slot_texts,
    summary_line,
    write_outputs,
)
from reelline.media.images.model import (
    MANIFEST_VERSION,
    CostCapReached,
    Evaluation,
    ImageError,
    Pick,
    SlotBudgetReached,
    no_pick_reason,
)
from reelline.media.images.picker import Picker, generation_seed
from reelline.media.images.query import parse_query, unnamed_entities
from reelline.media.script_markup import Script, image_slots


def resolve_reel(
    script: Script,
    reel_dir: Path,
    picker_factory: Callable[[], Picker],
    reel_name: str,
    refresh: bool = False,
    reimage: AbstractSet[int] = frozenset(),
    log: Callable[[str], None] = print,
    cooldown: AbstractSet[str] = frozenset(),
    context: str = "",
    skip: AbstractSet[tuple[int, int]] = frozenset(),
) -> dict:
    """Resolve every picture slot (a paragraph's first @image, then its extra `| word` ones)
    and write images.json + attributions.txt. One entry per slot, in text order; the extra
    ones carry "slot" (1, 2) and "anchor". A slot without an acceptable match is
    "no_candidate": the renderer keeps the previous picture on screen.
    cooldown: image ids shown in recent reels (cooldown.py), not picked again except an
    organization's own logo; context: the brief, which with the script's text decides
    whether a logo query's organization is named; skip: (paragraph, slot) pairs not to
    resolve (extra pictures too close to the one before, scripts/render_reel.py)."""
    named_in = "\n".join([context, *(f"{p.display}\n{p.card or ''}" for p in script.paragraphs)])
    manifest_path = reel_dir / "images.json"
    previous = None if refresh else load_manifest(manifest_path)
    prev_by_key = {(p["paragraph"], p.get("slot", 0)): p for p in (previous or {}).get("paragraphs", [])}
    texts = slot_texts(script)
    picker: Picker | None = None
    library: Library | None = None
    entries, generated_for = [], []
    for ref in image_slots(script):
        n, k, directive = ref.paragraph, ref.slot, ref.directive
        para = script.paragraphs[n - 1]
        before = entries[-1] if entries else None
        value = directive.raw if directive else None
        extra = k > 0
        where = {"slot": k, "anchor": para.more_images[k - 1].anchor} if extra else {}
        tag = f"¶{n}" + (f".{k}" if extra else "")
        prev = prev_by_key.get((n, k))
        if prev and (prev.get("value") != value or prev["status"] == "skipped"):
            prev = None
        if directive is None or directive.kind == "none":
            entries.append({"paragraph": n, "value": value, "status": "none"})
            continue
        if (n, k) in skip:
            reason = f"starts less than {CANDIDATES['min_slot_s']:g}s after the picture before it: skipped"
            log(f"Images {tag}: {reason}")
            entries.append({"paragraph": n, **where, "value": value, "status": "skipped", "reason": reason})
            continue
        if prev and n not in reimage:
            library = library or Library()
            if prev["status"] != "image" or (library.root / prev["file"]).is_file():
                entries.append({**prev, "cached": True})
                continue
            log(f"NOTE: {tag}: cached image {prev['image_id']} is missing from the library; picking again")
        picker = picker or picker_factory()
        library = picker.library
        excluded = list((prev or {}).get("excluded", []))
        if n in reimage and prev and prev.get("image_id") and prev["image_id"] not in excluded:
            excluded.append(prev["image_id"])
        display = texts[n, k]
        card = para.card if not extra else None
        if directive.kind == "lib":
            record = library.get(directive.value)
            if record is None:
                raise ImageError(f"{tag}: @image lib:{directive.value}: no such library image (scripts/images.py list)")
            if record["status"] != "accepted":
                raise ImageError(f"{tag}: @image lib:{directive.value} is rejected ({record['reason']})")
            query = record["queries"][0] if record["queries"] else " ".join(record["tags"])
            ev = picker.evaluate_record(record, display, card, query, pinned=True)
            if ev.status != "pass":
                log(
                    f"WARNING: {tag}: pinned lib:{directive.value} fails the vision check ({', '.join(ev.reasons)}); using it anyway"
                )
            entries.append({**image_entry(n, value, ev, picker.screen, excluded, pinned=True), **where})
            continue
        log(f"Images {tag}: {directive.value!r}")
        query = parse_query(directive.value)
        own_logo = query.logo and query.named
        unnamed = unnamed_entities(query, named_in) if own_logo else []
        if unnamed:
            reason = f"a logo of {', '.join(unnamed)}, which the script and brief don't name: skipped"
            log(f"  no image for {directive.value!r}, idle screen: {reason}")
            entries.append(
                {
                    "paragraph": n,
                    **where,
                    "value": value,
                    "status": "no_candidate",
                    "reason": reason,
                    "excluded": excluded,
                }
            )
            continue
        avoid = set(excluded) | (set() if own_logo else set(cooldown))
        if CANDIDATES["avoid_adjacent_repeat"] and before and before["status"] == "image" and before["value"] != value:
            avoid.add(before["image_id"])
        if extra and CANDIDATES["avoid_reel_repeat"]:
            avoid |= {e["image_id"] for e in entries if e["status"] == "image" and e["value"] != value}
        result = picker.pick(
            directive.value,
            display,
            card,
            generation_seed(reel_name, str(n) + (f".{k}" if extra else ""), directive.value),
            avoid,
            **({"extra": True} if extra else {}),
        )
        if result.generated:
            generated_for.append(n)
        if result.chosen:
            entries.append({**image_entry(n, value, result.chosen, picker.screen, excluded, pinned=False), **where})
        else:
            entries.append(
                {
                    "paragraph": n,
                    **where,
                    "value": value,
                    "status": "no_candidate",
                    "reason": result.reason,
                    "excluded": excluded,
                }
            )
    manifest = {
        "version": MANIFEST_VERSION,
        "reel": reel_name,
        "paragraphs": entries,
        "generated_fallback_used": sorted({e["paragraph"] for e in entries if e.get("source") == "generated"}),
        "generated_fallback_tried": generated_for,
    }
    write_outputs(reel_dir, manifest)
    return manifest


# Old import path: everything that used to live here.
__all__ = [
    "MANIFEST_VERSION",
    "SOURCES",
    "CostCapReached",
    "Evaluation",
    "ImageError",
    "Library",
    "Pick",
    "Picker",
    "SlotBudgetReached",
    "attribution_lines",
    "frame",
    "generation_seed",
    "image_entry",
    "load_manifest",
    "no_pick_reason",
    "resolve_reel",
    "slot_texts",
    "summary_line",
    "write_outputs",
]
