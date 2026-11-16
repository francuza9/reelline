"""The runner: a list of steps run in order over one Context, with the repair loop and dry runs.

Repair: a check (a step with `judges`) that rejects artifacts sends the work back to the step that
made the earliest of them (the first step of the run making it without needing it) and runs again
from there through the check, with the reasons in ctx.failures; steps.<check>.repair_rounds such
rounds at most. A round is named by the steps it reruns that make a judged artifact ("story+punchup",
"punchup"; the first run is "first"), and the passes inside it are stored under that name
("<route>/<key>"), so a resumed run goes on inside the right round. An error of the check's
`repair_errors` during a repair round ends it: the last version that ran to the end is kept, the
error stored under the check's repair_error_key (a resumed run doesn't repeat that round).

Each check's run is a Round (its route and the artifacts as they were then): `outcome.rounds`, also
on `ctx.state["rounds"]` while the run goes, so a caller handling a step's error still sees them.

Sessions: consecutive steps sharing a `session` name are handed together to ctx.sessions[name]
(steps, ctx) -> StepResult when the context has one (The host's script session: the scripts row and
its passes), else run here.

Dry run: a step that `sends` (review, publish) is skipped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from reelline.pipeline.step import Context, Step, StepResult


@dataclass
class Round:
    route: str
    artifacts: dict[str, Any]
    rejects: dict[str, list[str]]


@dataclass
class RunOutcome:
    status: str = "ok"  # ok | wait | failed
    detail: str | None = None
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    rounds: list[Round] = field(default_factory=list)
    repair_error: str | None = None
    stopped_at: str | None = None


def originator(steps: list[Step], artifact: str, upto: int) -> int | None:
    """The index of the first step before `upto` that makes `artifact` without needing it."""
    for i, s in enumerate(steps[:upto]):
        if artifact in s.makes and artifact not in s.needs:
            return i
    return None


class Runner:
    def __init__(self, steps: list[Step], log: Callable[[str], None] | None = None):
        self.steps = list(steps)
        self.log = log

    def run(self, ctx: Context) -> RunOutcome:
        log = self.log or ctx.log
        out = RunOutcome()
        ctx.state.setdefault("rounds", out.rounds)
        out.rounds = ctx.state["rounds"]
        spans = self._spans()  # check index -> (the first step it may send work back to, itself)
        i = 0
        while i < len(self.steps):
            step = self.steps[i]
            if step.session and step.session in ctx.sessions:
                j = i
                while j < len(self.steps) and self.steps[j].session == step.session:
                    j += 1
                result = ctx.sessions[step.session](self.steps[i:j], ctx)
                out.ran += [s.name for s in self.steps[i:j]]
                if not self._take(result, step, ctx, out):
                    return out
                i = j
                continue
            if ctx.dry_run and step.sends:
                log(f"{step.name}: skipped (dry run)")
                out.skipped.append(step.name)
                i += 1
                continue
            ctx.prefix = f"{ctx.route}/" if any(a <= i <= b for a, b in spans.values()) else ""
            result = step.run(ctx)
            out.ran.append(step.name)
            if not self._take(result, step, ctx, out):
                return out
            if step.judges:
                self._repair(i, result, ctx, out, log)
            i += 1
        ctx.prefix = ""
        return out

    def _spans(self) -> dict[int, tuple[int, int]]:
        spans = {}
        for i, s in enumerate(self.steps):
            if s.judges:
                starts = [k for a in s.judges if (k := originator(self.steps, a, i)) is not None]
                spans[i] = (min(starts) if starts else i, i)
        return spans

    @staticmethod
    def _take(result: StepResult, step: Step, ctx: Context, out: RunOutcome) -> bool:
        """Merge a step's result; False: the run stops here."""
        ctx.artifacts.update(result.made)
        if result.status == "skipped":
            out.skipped.append(step.name)
        if result.status in ("failed", "wait"):
            out.status, out.detail, out.stopped_at = result.status, result.detail, step.name
            return False
        return True

    @staticmethod
    def _record(result: StepResult, ctx: Context, out: RunOutcome) -> None:
        out.rounds.append(Round(ctx.route, dict(ctx.artifacts), dict(result.rejects)))

    def _repair(self, c: int, result: StepResult, ctx: Context, out: RunOutcome, log: Callable[[str], None]) -> None:
        check = self.steps[c]
        self._record(result, ctx, out)
        if not result.rejects:
            return
        if check.repair_error_key in ctx.store:
            out.repair_error = ctx.store.get(check.repair_error_key)
            log(f"  repair round failed in the earlier run ({out.repair_error}); keeping the first version, flagged")
            return
        rounds = 0
        while result.rejects and rounds < check.repair_rounds:
            makers = [k for a in result.rejects if (k := originator(self.steps, a, c)) is not None]
            if not makers:  # made before this run (its inputs): nothing here to send it back to
                log(f"  {check.name}: {', '.join(result.rejects)} rejected; no step of this run makes it")
                break
            rounds += 1
            back = min(makers)
            judged = {originator(self.steps, a, c) for a in check.judges}
            route = "+".join(self.steps[k].store_key for k in range(back, c) if k in judged)
            if result.note:
                log(result.note)
            kept = dict(ctx.artifacts)
            ctx.failures, ctx.route, ctx.prefix = dict(result.rejects), route, f"{route}/"
            try:
                for step in self.steps[back : c + 1]:
                    result = step.run(ctx)
                    ctx.artifacts.update(result.made)
                    if result.status in ("failed", "wait"):
                        raise RuntimeError(f"{step.name} {result.status} in a repair round: {result.detail}")
                self._record(result, ctx, out)
            except check.repair_errors as exc:
                out.repair_error = str(exc)
                ctx.store.put(check.repair_error_key, str(exc))
                ctx.artifacts.clear()
                ctx.artifacts.update(kept)
                log(f"  repair round failed ({exc}); keeping the first version, flagged")
                break
            finally:
                ctx.failures, ctx.prefix = {}, ""
        ctx.route = "first"
