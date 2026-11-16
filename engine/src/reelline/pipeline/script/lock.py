"""Display text and the draft lock: draft sentences compared with the final script's."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from reelline.config import conf
from reelline.media.script_markup import (
    Script,
)
from reelline.pipeline.script.base import _SENTENCE_END


def sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        out += [s.strip() for s in _SENTENCE_END.split(line.strip()) if s.strip()]
    return out


def normalize(sentence: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", sentence.casefold()).split())


def display_text(script: Script) -> str:
    """The final script's display text without the tag, the flirt, details and share lines,
    one line per paragraph."""
    return "\n".join(
        p.display for p in script.paragraphs if not p.tag and not p.flirt and not p.details and not p.share
    )


def flirt_line(script: Script) -> str | None:
    return next((p.display for p in script.paragraphs if p.flirt), None)


def details_line(script: Script) -> str | None:
    return next((p.display for p in script.paragraphs if p.details), None)


def share_line(script: Script) -> str | None:
    return next((p.display for p in script.paragraphs if p.share), None)


def draft_lock(draft: str, script: Script) -> dict:
    """{"unchanged": [...], "changed": [{"draft", "final", "ratio"}], "dropped": [...],
    "inserted": [...], "final": [{"text", "origin"}], "counts": {...}}: draft sentences
    matched to the final's, in order. Each final sentence's origin is "draft"
    (unchanged), "changed" (a draft sentence punch-up edited) or "punchup" (inserted)."""
    draft_s = sentences(draft)
    final_s = sentences(display_text(script))
    final_n = [normalize(s) for s in final_s]
    used: set[int] = set()
    report = {"unchanged": [], "changed": [], "dropped": [], "inserted": []}
    pointer = 0
    for d in draft_s:
        nd = normalize(d)
        best, best_ratio = None, 0.0
        for j in range(pointer, min(len(final_s), pointer + conf().steps.script.lookahead + 1)):
            if j in used:
                continue
            ratio = 1.0 if final_n[j] == nd else SequenceMatcher(None, nd, final_n[j]).ratio()
            if ratio > best_ratio:
                best, best_ratio = j, ratio
            if ratio >= conf().steps.script.same_ratio:
                break
        if best is None or best_ratio < conf().steps.script.changed_ratio:
            report["dropped"].append(d)
            continue
        used.add(best)
        pointer = best + 1
        if best_ratio >= conf().steps.script.same_ratio:
            report["unchanged"].append(d)
        else:
            report["changed"].append({"draft": d, "final": final_s[best], "ratio": round(best_ratio, 3)})
    report["inserted"] = [s for j, s in enumerate(final_s) if j not in used]
    changed_finals = {c["final"] for c in report["changed"]}
    report["final"] = [
        {"text": s, "origin": "punchup" if j not in used else ("changed" if s in changed_finals else "draft")}
        for j, s in enumerate(final_s)
    ]
    report["counts"] = {
        "draft": len(draft_s),
        "final": len(final_s),
        "unchanged": len(report["unchanged"]),
        "changed": len(report["changed"]),
        "dropped": len(report["dropped"]),
        "inserted": len(report["inserted"]),
    }
    return report


# ---- Jokes memory ----------------------------------------------------------------------
