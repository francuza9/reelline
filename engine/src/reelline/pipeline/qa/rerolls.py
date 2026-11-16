"""The QA flow: TTS re-roll of low-score paragraphs, the checks, one re-render on failure."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reelline.config import conf
from reelline.pipeline.qa.base import QA, Check, QaError, Report, Subject
from reelline.pipeline.qa.opening import low_words
from reelline.pipeline.qa.vision import Checker


def take_files(tts_dir: Path, n: int) -> list[Path]:
    """Paragraph n's take: pNN.wav/.json, plus pNNb.wav/.json (the rest after an opening
    paragraph's first sentence, scripts/render_reel.py paragraph_audio) when there."""
    files = [tts_dir / f"p{n:02d}.wav", tts_dir / f"p{n:02d}.json"]
    rest = [tts_dir / f"p{n:02d}b.wav", tts_dir / f"p{n:02d}b.json"]
    return files + [f for f in rest if f.is_file()]


def take_cost(tts_dir: Path, n: int) -> float | None:
    """The cached take's TTS cost (from its token counts; both parts of an opening
    paragraph's), None without one."""
    from reelline.providers import LLMUsage, cost_usd

    metas = [f for f in take_files(tts_dir, n) if f.suffix == ".json" and f.is_file()]
    if not metas:
        return None
    ms = [json.loads(f.read_text(encoding="utf-8")) for f in metas]
    return cost_usd(
        conf().models.tts.model,
        LLMUsage(sum(m.get("input_tokens", 0) for m in ms), sum(m.get("output_tokens", 0) for m in ms), 0),
    )


def take_score(rows: list[dict], n: int) -> tuple[int, float]:
    """(words below QA["low_score"], minimum word score; 1.0 with none) for paragraph n."""
    low = [r["score"] for r in low_words(rows) if r["paragraph"] == n]
    return len(low), min(low, default=1.0)


def better_take(old: tuple[int, float], new: tuple[int, float]) -> str:
    """ "new" or "old": fewer low words wins, then the higher minimum (a full tie keeps the new)."""
    if new[0] != old[0]:
        return "new" if new[0] < old[0] else "old"
    return "old" if old[1] > new[1] else "new"


@dataclass
class Outcome:
    rendered: Any  # reelline.pipeline.produce.Rendered, the final render
    report: Report | None
    history: dict
    renders: list = field(default_factory=list)  # every render after the first, in order
    failed_calls: list = field(default_factory=list)  # what a failed re-roll render spent

    @property
    def calls(self) -> list[dict]:
        """Paid model calls QA caused: its renders' image and TTS calls (a re-roll's
        takes as tts_reroll, a failed re-roll's too) and its vision calls."""
        rows = [c for r in self.renders for c in (r.image_calls or []) + (r.tts_calls or [])] + self.failed_calls
        return rows + [c for rep in self.history["reports"] for c in rep.calls]

    @property
    def tts_new_cost_usd(self) -> float:
        return sum(r.tts_new_cost_usd or 0.0 for r in self.renders)


def run_flow(
    rendered,
    rerender: Callable[[list[int]], Any],
    subject_for: Callable[[Any], Subject],
    checker: Checker,
    tag_paragraph: int | None,
    log: Callable[[str], None] = print,
) -> Outcome:
    """Re-roll, check, re-render on failure (module docstring). `rerender(regen)` is a
    render with those paragraphs' TTS redone (none: from the cache); it raises
    reelline.pipeline.produce.RenderFailed. The reports go into history["reports"] (Report
    objects; qa_json() writes them out)."""
    history: dict = {"rerolls": [], "renders": [], "reports": []}
    outcome = Outcome(rendered, None, history)

    def render(regen: list[int], reason: str):
        log(f"QA: rendering again ({reason})")
        try:
            r = rerender(regen)
        except Exception as exc:  # the caller still records what QA spent so far
            if regen:
                reroll_takes(getattr(exc, "paid_calls", []))
            exc.qa_outcome = outcome
            raise
        if regen:
            reroll_takes(r.tts_calls or [])
        outcome.renders.append(r)
        history["renders"].append(
            {"reason": reason, "regen": regen, "tts_new_cost_usd": r.tts_new_cost_usd, "duration_s": r.duration}
        )
        return r

    paragraphs = sorted({r["paragraph"] for r in low_words(rendered.check_these)} - {tag_paragraph})
    if paragraphs and QA["reroll_rounds"] > 0:
        try:
            rendered = reroll(rendered, paragraphs, render, history, log)
        except Exception as exc:
            if not getattr(exc, "reroll_restored", False):
                raise
            # The re-voice failed (a TTS timeout, an API error): the old takes are back,
            # the first render stands, the checks go on (a broken file fails them and
            # gets the re-render from the cache).
            outcome.failed_calls += getattr(exc, "paid_calls", None) or []
            history["rerolls"].append({"paragraphs": [], "failed": paragraphs, "error": f"{type(exc).__name__}: {exc}"})
            log(f"QA: the re-roll failed ({type(exc).__name__}: {exc}); keeping the old takes")
    for attempt in range(QA["rerenders"] + 1):
        try:
            report = checker.run(subject_for(rendered))
        except QaError as exc:  # QA itself broke: say so, and don't re-render over it
            report = Report([Check("qa", "error", "fail", str(exc), None, [f"QA could not run: {exc}"])])
            history["reports"].append(report)
            log(f"QA: ❌ could not run: {exc}")
            break
        history["reports"].append(report)
        log(f"QA: {'✅ pass' if report.status == 'pass' else '❌ ' + '; '.join(report.failures)}")
        if report.status == "pass" or attempt == QA["rerenders"]:
            break
        rendered = render([], "QA failed: " + "; ".join(report.failures))
    outcome.rendered, outcome.report = rendered, report
    return outcome


def reroll_takes(calls: list[dict]) -> None:
    """A re-roll render's fresh TTS clips are re-roll takes (purpose tts_reroll)."""
    for c in calls:
        if c["purpose"] == "tts":
            c["purpose"] = "tts_reroll"


def reroll(rendered, paragraphs: list[int], render: Callable, history: dict, log: Callable[[str], None]):
    """One re-roll round: new takes for `paragraphs`, then per paragraph the better take."""
    tts_dir = rendered.mp4.parent / "tts"
    backup = tts_dir / "qa_old_takes"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)
    old_cost = {}
    for n in paragraphs:
        old_cost[n] = take_cost(tts_dir, n)
        for f in take_files(tts_dir, n):
            if f.is_file():
                shutil.copy2(f, backup / f.name)

    def restore(ns):
        for n in ns:
            for f in take_files(tts_dir, n):
                if (backup / f.name).is_file():
                    shutil.copy2(backup / f.name, f)

    old_rows = rendered.check_these
    log(f"QA: re-rolling the TTS of {', '.join(f'¶{n}' for n in paragraphs)} (words below {QA['low_score']})")
    try:
        new = render(paragraphs, "re-roll " + ", ".join(f"¶{n}" for n in paragraphs))
    except Exception as exc:
        restore(paragraphs)
        shutil.rmtree(backup)
        exc.reroll_restored = True  # run_flow() goes on with the old takes
        raise
    entry = {"paragraphs": []}
    keep_old = []
    for n in paragraphs:
        o, w = take_score(old_rows, n), take_score(new.check_these, n)
        kept = better_take(o, w)
        if kept == "old":
            keep_old.append(n)
        entry["paragraphs"].append(
            {
                "paragraph": n,
                "kept": kept,
                "old": {"low_words": o[0], "min_score": o[1], "tts_cost_usd": old_cost[n]},
                "new": {"low_words": w[0], "min_score": w[1], "tts_cost_usd": take_cost(tts_dir, n)},
            }
        )
        log(f"QA: ¶{n} old take {o[0]} low word(s) (min {o[1]:.2f}), new {w[0]} (min {w[1]:.2f}): keeping the {kept}")
    history["rerolls"].append(entry)
    if keep_old:
        restore(keep_old)
        new = render([], "old take kept for " + ", ".join(f"¶{n}" for n in keep_old))
    shutil.rmtree(backup)
    return new
