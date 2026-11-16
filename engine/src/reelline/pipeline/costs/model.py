"""Cost rows, the world they point into, and attribution of a row to a step and an outcome (pure)."""

from __future__ import annotations

from dataclasses import dataclass, field

from reelline.config import conf


@dataclass(frozen=True)
class Row:
    """One llm_calls row."""

    id: int
    at: str  # called_at, UTC ISO
    purpose: str
    model: str
    usd: float | None
    script_id: int | None = None
    reel_id: int | None = None
    pick_id: int | None = None
    job_id: int | None = None
    manual: bool = False
    estimated: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_thinking: int | None = None
    error: str | None = None  # a failed try of a dropped connection (reelline/providers/retry.py)


@dataclass
class World:
    """What the rows point at (plain dicts, by id): reels (pick_id, script_id, status,
    created_at), scripts (pick_id, status), picks (story_id, slot_date, slot_time,
    status), jobs (kind, status), story titles."""

    reels: dict[int, dict] = field(default_factory=dict)
    scripts: dict[int, dict] = field(default_factory=dict)
    picks: dict[int, dict] = field(default_factory=dict)
    jobs: dict[int, dict] = field(default_factory=dict)
    titles: dict[int, str] = field(default_factory=dict)

    def __post_init__(self):
        self.last_reel_by_script: dict[int, int] = {}
        self.last_reel_by_pick: dict[int, int] = {}
        self.last_script_by_pick: dict[int, int] = {}
        for rid in sorted(self.reels):
            self.last_reel_by_script[self.reels[rid]["script_id"]] = rid
            self.last_reel_by_pick[self.reels[rid]["pick_id"]] = rid
        for sid in sorted(self.scripts):
            self.last_script_by_pick[self.scripts[sid]["pick_id"]] = sid

    def reel_status(self, reel_id: int) -> str:
        """The reel's status; an approved reel whose pick is posted is published (until
        step 7 gives reels their own published status)."""
        reel = self.reels[reel_id]
        pick = self.picks.get(reel["pick_id"]) or {}
        if reel["status"] == "approved" and pick.get("status") == "posted":
            return "published"
        return reel["status"]


def matches(purpose: str, patterns) -> bool:
    return any(purpose.startswith(p[:-1]) if p.endswith("*") else purpose == p for p in patterns)


# ---- Attribution (pure) ------------------------------------------------------------------


@dataclass(frozen=True)
class Cost:
    """A row with where it counts."""

    row: Row
    step: str  # costs.steps key
    provider: str  # costs.providers key, or "other"
    google_kind: str | None
    outcome: str  # costs.outcomes key
    detail: str  # the reel's status, or a costs.no_reel key, "assess", "manual", "unlinked"
    reel_id: int | None  # the reel it counts under
    pick_id: int | None

    @property
    def usd(self) -> float:
        return self.row.usd or 0.0


def step_of(row: Row, world: World) -> str:
    if row.manual:
        return "manual"
    job = world.jobs.get(row.job_id)
    if job is not None and job["kind"] in conf().costs.regeneration_jobs:
        return "regenerations"
    for key, _, patterns in conf().costs.steps:
        if matches(row.purpose, patterns):
            return key
    return "other"


def provider_of(row: Row) -> tuple[str, str | None]:
    """(provider key, Google's kind label or None)."""
    for key, _, prefixes in conf().costs.providers:
        if row.model.startswith(tuple(prefixes)):
            if key != "google":
                return key, None
            kind = next(
                (label for label, pats in conf().costs.google_kinds if matches(row.purpose, pats)),
                conf().costs.google_text,
            )
            return key, kind
    return "other", None


def outcome_of(row: Row, world: World) -> tuple[str, str, int | None, int | None]:
    """(outcome, detail, reel it counts under, pick) for a row (the module docstring)."""
    if row.manual:
        return "manual", "manual", row.reel_id, row.pick_id
    if matches(row.purpose, conf().costs.overhead_purposes):
        return "overhead", "assess", None, None
    script = world.scripts.get(row.script_id)
    pick_id = row.pick_id or (script["pick_id"] if script else None)
    reel_id = row.reel_id if row.reel_id in world.reels else None
    if reel_id is None and script is not None:
        reel_id = world.last_reel_by_script.get(row.script_id)
    if reel_id is None and row.script_id is None and pick_id is not None:
        reel_id = world.last_reel_by_pick.get(pick_id)
    if reel_id is not None:
        status = world.reel_status(reel_id)
        return conf().costs.reel_outcomes.get(status, "waste"), status, reel_id, world.reels[reel_id]["pick_id"]
    if pick_id is None:
        return "unattributed", "unlinked", None, None
    pick = world.picks.get(pick_id) or {}
    job = world.jobs.get(row.job_id) or {}
    if script is not None and script["status"] == "failed":
        return "waste", "script", None, pick_id
    if job.get("status") == "failed":
        return "waste", "job", None, pick_id
    if pick.get("status") in ("rejected", "skipped"):
        return "waste", "pick", None, pick_id
    if script is not None and world.last_script_by_pick.get(pick_id) != row.script_id:
        return "waste", "replaced", None, pick_id
    return "pending", "pending", None, pick_id


def attribute(rows: list[Row], world: World) -> list[Cost]:
    out = []
    for row in rows:
        provider, kind = provider_of(row)
        outcome, detail, reel_id, pick_id = outcome_of(row, world)
        out.append(Cost(row, step_of(row, world), provider, kind, outcome, detail, reel_id, pick_id))
    return out


def counts_for_reel(c: Cost) -> int | None:
    """The reel whose total `c` is part of (the top reels, the review's cost line)."""
    return c.reel_id if c.outcome != "manual" else None
