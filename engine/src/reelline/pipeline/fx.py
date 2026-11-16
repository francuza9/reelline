"""The fact graphics' fact-check (channel docs/fx.md "In a reel"): each @fx value in the final
script, checked against the story's source like the script and the end card.

check_fx() sends one "sentence" per @fx (reelline/pipeline/factcheck.py fact_check(), purpose
fx_factcheck): the card's fact as it shows it (reelline/media/fx.py FxDirective.text), with the
origin "fx", its template's label (LABELS) and the paragraph it's on as context, numbered
"S<n> (fx, <label>, on: „<paragraph>“)" in the prompt (fact-check prompt 5, "Fact
graphics"). An fx is shown only with at least one supported claim and no failing one: one
that fails, or gets no claim at all, is dropped, never shown, and the review says why. A
failed fact-check call drops them all (the script itself goes on). No repair round: an fx
is decoration, the script says the fact anyway.

reelline/pipeline/script.py write_script() runs it on the final version (scripts.passes["fx"]) and
finish() writes what it found to <script>.fx.json (reelline/media/fx.py write_checked()), which
render_reel.py reads: only the @fx listed there as shown get a card.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from reelline.media.fx import fx_path, write_checked
from reelline.media.script_markup import Script

# The templates' labels in the fact-check prompt ("S2 (fx, application deadline, on: ...)").
LABELS = {
    "deadline": "application deadline",
    "start": "start date",
    "age": "age range of who can take part",
    "money": "an amount of money (what it is: the paragraph says)",
    "places": "number of places",
    "duration": "how long it lasts",
    "condition": "a condition to take part",
    "free": "it's free",
    "certificate": "participants get a certificate",
    "prize": "the prize",
    "location": "where in Georgia it is",
    "abroad": "the country it takes you to",
    "apply": "the website to apply on",
    "format": "online or in person",
    "steps": "the selection or application steps, in order",
}


@dataclass
class FxCheck:
    """What the fact-check let through (shown) and what it dropped, with why."""

    shown: list[dict] = field(default_factory=list)  # [{paragraph, raw, template, text}]
    dropped: list[dict] = field(default_factory=list)  # [... and reason]
    factcheck: dict | None = None  # FactCheck.to_json(), None without a call
    error: str | None = None  # a failed call: everything dropped
    cost_usd: float = 0.0

    def to_json(self) -> dict:
        return {"shown": self.shown, "dropped": self.dropped, "factcheck": self.factcheck, "error": self.error}

    @classmethod
    def from_json(cls, d: dict) -> FxCheck:
        return cls(d.get("shown", []), d.get("dropped", []), d.get("factcheck"), d.get("error"))


def entries(script: Script) -> list[dict]:
    """One per @fx, in the script's order: {paragraph, raw, template, text}."""
    return [
        {"paragraph": n, "raw": p.fx.raw, "template": p.fx.template, "text": p.fx.text}
        for n, p in enumerate(script.paragraphs, 1)
        if p.fx is not None
    ]


def fx_report(script: Script) -> dict:
    """The fact-check's input (a draft-lock report's "final"): one sentence per @fx."""
    return {
        "final": [
            {
                "text": e["text"],
                "origin": "fx",
                "label": LABELS[e["template"]],
                "context": script.paragraphs[e["paragraph"] - 1].display,
                **e,
            }
            for e in entries(script)
        ]
    }


def check_fx(
    conn: sqlite3.Connection | None,
    client,
    source_text: str,
    brief: str,
    script: Script,
    *,
    script_id: int | None = None,
    log=print,
) -> FxCheck:
    """The fx's fact-check (module docstring). No @fx: no call."""
    from reelline.pipeline.factcheck import FactCheckError, fact_check

    found = entries(script)
    if not found:
        return FxCheck()
    report = fx_report(script)
    try:
        fc = fact_check(conn, client, source_text, brief, report, script_id, purpose="fx_factcheck")
    except FactCheckError as exc:
        log(f"  fx fact-check failed ({exc}); no fact graphic is shown")
        return FxCheck([], [{**e, "reason": f"the fact-check failed: {exc}"} for e in found], None, str(exc))
    shown, dropped = [], []
    for n, e in enumerate(found, 1):
        claims = [c for c in fc.claims if c["sentence"] == n]
        failing = [c for c in claims if c["verdict"] in ("unsupported", "distorted")]
        if failing:
            c = failing[0]
            dropped.append(
                {**e, "reason": f"{c['verdict']}: {c['claim']}" + (f" ({c['note']})" if c.get("note") else "")}
            )
        elif not any(c["verdict"] == "supported" for c in claims):
            dropped.append({**e, "reason": "the fact-check found no supported claim in it"})
        else:
            shown.append(e)
    for d in dropped:
        log(f"  fx: left off ¶{d['paragraph']} {d['template']} „{d['text']}“ ({d['reason']})")
    log(f"  fx fact-check: {len(shown)} shown, {len(dropped)} dropped")
    return FxCheck(shown, dropped, fc.to_json(), None, fc.cost_usd)


def write_file(script_path, script_id: int | None, checked: FxCheck):
    """<script>.fx.json next to the script: what render_reel.py may show."""
    return write_checked(fx_path(script_path), checked.shown, checked.dropped, script_id=script_id, error=checked.error)


def summary_lines(checked: dict | None, rendered: dict | None = None) -> list[str]:
    """The review's fact-graphics lines from a stored FxCheck (scripts.passes["fx"]) and, when
    there is one, the render's fx_events.json (what timing left out)."""
    if checked is None:
        return []
    shown, dropped = checked.get("shown", []), checked.get("dropped", [])
    if not shown and not dropped:
        return ["🎞 Fact graphics: none in the script"]
    left = {(x["paragraph"], x["raw"]): x["reason"] for x in (rendered or {}).get("left", [])}
    on = [e for e in shown if (e["paragraph"], e["raw"]) not in left]
    out = [
        f"🎞 Fact graphics: {'✅' if not dropped and not left else '⚠️'} {len(on)} shown"
        + (": " + ", ".join(f"{e['template']} „{e['text']}“ (¶{e['paragraph']})" for e in on) if on else "")
    ]
    out += [f"  - left off {d['template']} „{d['text']}“ (¶{d['paragraph']}): {d['reason']}" for d in dropped]
    out += [
        f"  - not rendered {e['template']} „{e['text']}“ (¶{e['paragraph']}): {left[(e['paragraph'], e['raw'])]}"
        for e in shown
        if (e["paragraph"], e["raw"]) in left
    ]
    return out
