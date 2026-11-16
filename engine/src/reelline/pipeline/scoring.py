"""Eligibility gates and the effective score for pick (all numbers in PICK).

Pure functions over Candidate (a story plus its assessment) and PastPick (the pick
history), evaluated at a slot's time (slot_datetime(): the channel's local time). The
channel's topic (steps.pick.topic) is an opportunity people can still use. A candidate that fails any gate is never picked:
  - assessed under an older assess_prompt_version (re-assess it: `assess --stale`)
  - banned; any topic but the channel's (steps.pick.topic); story status not the expected one ("new" for
    ranking, "queued" for a queued pick's re-check); already posted
  - not an opportunity (nothing to apply for, register for or join); closed
  - marketing or low credibility; provider tier "other" (genuinely reputable providers
    only); not open to people in Georgia
  - a deadline fewer than min_deadline_days away (or past); an event date before the
    slot's day
  - an exam registration (edu_type exam) while another exam is picked within
    exam_every_days of the slot: one exam reel a week at most
  - no deadline and no event date ahead: older than max_age_days
  - repeats: the same canonical URL or same_program_key (exact, or difflib ratio
    >= repeat_fuzzy on the key or the title) picked, queued or posted within
    repeat_days
  - dismissed: status "dismissed" (turned down in a menu), or the same story (as
    above, no time limit) as a dismissed one

The weekly topic (steps.pick.weekly_topic, "tech"): its story is gated out except in the slot whose
publish day is steps.pick.tech_weekday (a queued one, already chosen, may go on any day); there
it must be big news and not niche, no older than tech_max_age_days, and its score
(tech_significance, hook mixed in) at least tech_min_score. Pick.ranking() ranks the tech
stories alone when any is eligible on that day, else the education ones as usual.

Hook: every rank score (education or tech) is (1 - hook_weight) * score + hook_weight *
hook_strength (with_hook()); a story assessed before the hook existed keeps its score.

Base score (education_base()): 0-10, steps.pick.score_points for the open status, the
reach (how many can apply), the provider tier (credibility) and the novelty, weighted
by steps.pick.score_weights in that order of importance. Effective score: the base; with
a deadline no aging and +deadline_bonus 5-14 days out; otherwise (no deadline) it
loses decay_per_day per day of age (from published_at, or the first-seen time when
that's all there is) unless an event date is still ahead; georgia_relevance
"unclear" loses unclear_relevance_penalty.

rank() orders the eligible (an exam always after every program or competition with the
same deadline), dedupe() sets aside the same story from a second outlet (the
lower-ranked copy), and contenders() finds the near-ties that go to a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from difflib import SequenceMatcher

from reelline.config import conf

LIVE_PICK_STATUSES = (
    "queued",
    "picked",
    "scripted",
    "rendered",
    "render_failed",
    "approved",
    "posted",
)  # not "rejected"


def tbilisi() -> timezone:
    return timezone(timedelta(hours=conf().channel.utc_offset_h))


def slot_datetime(day: date, hhmm: str) -> datetime:
    """A slot (Tbilisi date and HH:MM) as an aware UTC datetime."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tbilisi()).astimezone(UTC)


@dataclass(frozen=True)
class Candidate:
    story_id: int
    title: str
    source_id: str
    canonical_url: str
    status: str
    published_at: datetime | None
    date_source: str | None
    fetched_at: datetime
    topic: str
    banned: bool
    base_score: float
    same_program_key: str
    summary: str
    banned_reason: str | None = None
    edu_type: str | None = None
    provider: str | None = None
    provider_tier: str | None = None
    cost: str | None = None
    marketing_or_low_credibility: bool | None = None
    georgia_relevance: str | None = None
    deadline: date | None = None
    who_can_apply: str | None = None
    big_news: bool | None = None
    niche: bool | None = None
    prompt_version: int | None = None
    opportunity: bool | None = None
    open_status: str | None = None
    reach: str | None = None
    novelty: str | None = None
    event_date: date | None = None
    hook_strength: int | None = None
    tech_significance: int | None = None

    @property
    def reference_time(self) -> datetime:
        """What age is measured from: the first-seen time when no real date exists."""
        if self.date_source == "first_seen" or self.published_at is None:
            return self.fetched_at
        return self.published_at

    @property
    def has_deadline(self) -> bool:
        return self.topic == main_topic() and self.deadline is not None


def main_topic() -> str:
    """The topic the channel's stories are about (steps.pick.topic): ranked on the opportunity rules."""
    return conf().steps.pick.topic


def weekly_topic() -> str:
    """The topic that gets one slot a week (steps.pick.weekly_topic); "" for none."""
    return conf().steps.pick.weekly_topic


def education_base(c: Candidate) -> tuple[float, list[str]]:
    """The base score of an education story (0-10) and how it was made: steps.pick.score_points
    for its open status, reach, provider tier and novelty, each times its
    steps.pick.score_weights weight. A value with no points (unknown, closed, other) scores 0."""
    total, parts = 0.0, []
    for field_name, weight in conf().steps.pick.score_weights.items():
        value = getattr(c, field_name)
        points = conf().steps.pick.score_points[field_name].get(value or "", 0)
        total += weight * points
        parts.append(f"{value or '?'} {points:g}")
    return round(total, 2), parts


def tech_slot(slot_dt: datetime) -> bool:
    """Whether the slot's publish day (Tbilisi) is the weekly big-tech day (steps.pick.tech_weekday)."""
    return bool(weekly_topic()) and slot_dt.astimezone(tbilisi()).weekday() == conf().steps.pick.tech_weekday


def with_hook(score: float, c: Candidate, parts: list[str]) -> float:
    """The rank score with the hook folded in: (1 - hook_weight) * score + hook_weight *
    hook_strength; unchanged without a hook (a story assessed before) or with weight 0."""
    weight = conf().steps.pick.hook_weight
    if c.hook_strength is None or not weight:
        return score
    mixed = (1 - weight) * score + weight * c.hook_strength
    parts.append(f"hook {c.hook_strength} ({weight:.0%}): {score:.2f} -> {mixed:.2f}")
    return mixed


@dataclass(frozen=True)
class PastPick:
    story_id: int
    canonical_url: str
    same_program_key: str | None
    title: str
    topic: str | None
    status: str
    at: datetime  # the slot's time, or when it was queued
    edu_type: str | None = None


@dataclass
class Evaluation:
    candidate: Candidate
    gates: list[str] = field(default_factory=list)  # why it's out; empty = eligible
    effective: float | None = None
    parts: list[str] = field(default_factory=list)  # how the score was made
    days_left: int | None = None
    age_days: float | None = None

    @property
    def eligible(self) -> bool:
        return not self.gates


def _norm(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def similar(a: str | None, b: str | None) -> float:
    """difflib ratio of the normalized texts, or of their words sorted (so "erasmus
    mundus call 2027" matches "erasmus mundus 2027 call"), whichever is higher."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    words = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    return max(SequenceMatcher(None, a, b).ratio(), words)


def same_story(c: Candidate, past: PastPick) -> str | None:
    """How c is the same story as `past` (same URL, or same_program_key / title, exact
    or fuzzy at repeat_fuzzy), or None."""
    if past.canonical_url == c.canonical_url:
        return f"same URL as story {past.story_id} ({past.status})"
    if past.same_program_key and past.same_program_key == c.same_program_key:
        return f"same program as story {past.story_id} ({past.status}): {c.same_program_key!r}"
    key_ratio = similar(past.same_program_key, c.same_program_key)
    if key_ratio >= conf().steps.pick.repeat_fuzzy:
        return (
            f"like story {past.story_id} ({past.status}): {c.same_program_key!r} ~ "
            f"{past.same_program_key!r} ({key_ratio:.2f})"
        )
    title_ratio = similar(past.title, c.title)
    if title_ratio >= conf().steps.pick.repeat_fuzzy:
        return f"title like story {past.story_id} ({past.status}) ({title_ratio:.2f})"
    return None


def repeat_of(c: Candidate, history: list[PastPick], slot_dt: datetime) -> str | None:
    """Why c repeats a recent live pick, or None."""
    since = slot_dt - timedelta(days=conf().steps.pick.repeat_days)
    for past in history:
        if past.story_id == c.story_id or past.status not in LIVE_PICK_STATUSES or past.at < since:
            continue
        why = same_story(c, past)
        if why:
            return why
    return None


def exam_nearby(c: Candidate, history: list[PastPick], slot_dt: datetime) -> str | None:
    """For an exam registration: the other exam picked (live) within exam_every_days of the
    slot, else None."""
    if c.edu_type != "exam":
        return None
    window = timedelta(days=conf().steps.pick.exam_every_days)
    for past in history:
        if (
            past.edu_type == "exam"
            and past.story_id != c.story_id
            and past.status in LIVE_PICK_STATUSES
            and abs(past.at - slot_dt) < window
        ):
            day = past.at.astimezone(tbilisi()).date().isoformat()
            return f"exam: another exam reel within {conf().steps.pick.exam_every_days} days (story {past.story_id}, {day})"
    return None


def dismissed_twin(c: Candidate, dismissed: list[PastPick]) -> str | None:
    """Why c is the same story as a dismissed one (any time ago), or None."""
    for past in dismissed:
        if past.story_id != c.story_id:
            why = same_story(c, past)
            if why:
                return why
    return None


def evaluate(
    c: Candidate,
    slot_dt: datetime,
    history: list[PastPick],
    posted: set[int],
    expect_status: str = "new",
    dismissed: list[PastPick] = (),
    allow_tech: bool | None = None,
) -> Evaluation:
    """dismissed: the stories dismissed in menus (status "dismissed"); a story that is
    the same story as one of them is out too. allow_tech: whether a tech story may be
    ranked (default: the slot is the weekly tech slot; a queued story, already chosen,
    always may)."""
    if allow_tech is None:
        allow_tech = tech_slot(slot_dt) or expect_status != "new"
    ev = Evaluation(c)
    gates = ev.gates
    if c.prompt_version is not None and c.prompt_version < conf().steps.pick.assess_prompt_version:
        gates.append(f"assessed under the old rules (v{c.prompt_version}): re-assess it")
    if c.banned:
        gates.append(f"banned: {c.banned_reason or 'no reason given'}")
    weekly = weekly_topic()
    if weekly and c.topic == weekly:
        if not allow_tech:
            gates.append(f"topic {weekly}: only in the weekly {weekly} slot")
        elif not c.big_news:
            gates.append(f"{weekly}: not big news")
        elif c.niche:
            gates.append(f"{weekly}: niche")
    elif c.topic != main_topic():
        gates.append(f"topic {c.topic}: {main_topic()} only")
    if c.status != expect_status:
        gates.append(f"status {c.status}")
    if c.story_id in posted or c.status == "used":
        gates.append("already posted")

    slot_day = slot_dt.astimezone(tbilisi()).date()
    event_ahead = False
    if c.topic == main_topic():
        if c.opportunity is False:
            gates.append("not an opportunity to apply for")
        if c.open_status == "closed":
            gates.append("closed")
        if c.marketing_or_low_credibility:
            gates.append("marketing or low credibility")
        if c.provider_tier in (None, "other"):
            gates.append(f"provider not established (tier {c.provider_tier})")
        if c.georgia_relevance == "not_open":
            gates.append("not open to Georgians")
        if c.deadline is not None:
            ev.days_left = (c.deadline - slot_day).days
            if ev.days_left < 0:
                gates.append(f"deadline passed ({c.deadline.isoformat()})")
            elif ev.days_left < conf().steps.pick.min_deadline_days:
                gates.append(f"deadline in {ev.days_left} days (< {conf().steps.pick.min_deadline_days})")
        if c.event_date is not None:
            if c.event_date < slot_day:
                gates.append(f"event passed ({c.event_date.isoformat()})")
            else:
                event_ahead = True

    if not c.has_deadline:
        ev.age_days = max(0.0, (slot_dt - c.reference_time).total_seconds() / 86400)
        if ev.age_days > conf().steps.pick.max_age_days and not event_ahead:
            gates.append(f"older than {conf().steps.pick.max_age_days:g} days ({ev.age_days:.1f})")

    why = exam_nearby(c, history, slot_dt)
    if why:
        gates.append(why)
    why = repeat_of(c, history, slot_dt)
    if why:
        gates.append(f"repeat: {why}")
    why = dismissed_twin(c, dismissed)
    if why:
        gates.append(f"dismissed: {why}")

    if weekly and c.topic == weekly:
        age = max(0.0, (slot_dt - c.reference_time).total_seconds() / 86400)
        if age > conf().steps.pick.tech_max_age_days:
            gates.append(f"{weekly} story older than {conf().steps.pick.tech_max_age_days:g} days ({age:.1f})")
        significance = c.tech_significance or 0
        ev.parts.append(f"{weekly} significance {significance}")
        ev.effective = round(with_hook(float(significance), c, ev.parts), 2)
        if ev.effective < conf().steps.pick.tech_min_score and not gates:
            gates.append(f"{weekly} score {ev.effective:.2f} below {conf().steps.pick.tech_min_score:g}")
    if c.topic == main_topic():
        score, base_parts = education_base(c)
        ev.parts.append(f"base {score:g} ({', '.join(base_parts)})")
        if c.has_deadline:
            low, high = conf().steps.pick.deadline_bonus_days
            if ev.days_left is not None and low <= ev.days_left <= high:
                score += conf().steps.pick.deadline_bonus
                ev.parts.append(f"+{conf().steps.pick.deadline_bonus:g} deadline in {ev.days_left} days")
            else:
                ev.parts.append(f"deadline in {ev.days_left} days, no decay")
        elif event_ahead:
            ev.parts.append(f"event {c.event_date.isoformat()} ahead, no decay")
        else:
            decay = conf().steps.pick.decay_per_day * ev.age_days
            score -= decay
            ev.parts.append(
                f"-{decay:.2f} for {ev.age_days:.1f} days" + (" (first seen)" if c.date_source == "first_seen" else "")
            )
        if c.georgia_relevance == "unclear":
            score -= conf().steps.pick.unclear_relevance_penalty
            ev.parts.append(f"-{conf().steps.pick.unclear_relevance_penalty:g} relevance unclear")
        ev.effective = round(with_hook(score, c, ev.parts), 2)
    return ev


def rank(evaluations: list[Evaluation]) -> list[Evaluation]:
    """The eligible ones, best first (ties: higher base, newer, lower id); then each exam
    moved after every program or competition (any other kind) with the same deadline."""
    eligible = [e for e in evaluations if e.eligible]
    ranked = sorted(
        eligible,
        key=lambda e: (
            -e.effective,
            -e.candidate.base_score,
            -e.candidate.reference_time.timestamp(),
            e.candidate.story_id,
        ),
    )
    for exam in [e for e in ranked if e.candidate.edu_type == "exam" and e.candidate.deadline is not None]:
        same = [
            i
            for i, e in enumerate(ranked)
            if e.candidate.edu_type != "exam" and e.candidate.deadline == exam.candidate.deadline
        ]
        at = ranked.index(exam)
        if same and at < same[-1]:
            ranked.insert(same[-1], ranked.pop(at))
            exam.parts.append("after the programs and competitions with the same deadline")
    return ranked


def dedupe(ranked: list[Evaluation]) -> tuple[list[Evaluation], list[Evaluation]]:
    """Cross-source same-story de-dup: walking best first, a candidate that repeats a
    higher-ranked one (same URL, or same_program_key / title, exact or fuzzy as in
    repeat_of) is set aside with a "same story as #X" gate. Returns (kept, set aside)."""
    kept: list[Evaluation] = []
    aside: list[Evaluation] = []
    for e in ranked:
        c = e.candidate
        above = [
            PastPick(
                k.candidate.story_id,
                k.candidate.canonical_url,
                k.candidate.same_program_key,
                k.candidate.title,
                k.candidate.topic,
                "picked",
                datetime.max.replace(tzinfo=UTC),
            )
            for k in kept
        ]
        why = repeat_of(c, above, datetime.max.replace(tzinfo=UTC) - timedelta(days=1))
        if why:
            e.gates.append("same story as a higher-ranked candidate: " + why.replace(" (picked)", ""))
            aside.append(e)
        else:
            kept.append(e)
    return kept, aside


def contenders(ranked: list[Evaluation]) -> list[Evaluation]:
    """Near-ties at the top: effective >= contender_min and within contender_within
    below the top."""
    if not ranked:
        return []
    top = ranked[0].effective
    return [
        e
        for e in ranked
        if e.effective >= conf().steps.pick.contender_min
        and top - conf().steps.pick.contender_within <= e.effective <= top
    ]
