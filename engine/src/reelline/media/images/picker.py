"""Picker: the per-paragraph candidate search, vision checks and pick."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from collections.abc import Set as AbstractSet

from PIL import Image

from reelline.config import conf
from reelline.config.credentials import ConfigError, load_pexels, load_pixabay
from reelline.media.images import framing
from reelline.media.images.config import CANDIDATES, GENERATED, SOURCE_ORDER, SOURCES, VISION, Screen, screen
from reelline.media.images.framing import aspect_problem
from reelline.media.images.library import Library, image_id, key_id, query_words
from reelline.media.images.model import CostCapReached, Evaluation, Pick, SlotBudgetReached, no_pick_reason
from reelline.media.images.query import Query, names_entity, parse_query
from reelline.media.images.sources import Candidate, Commons, Pexels, Pixabay, Reject, SourceError, generate, open_image
from reelline.media.images.vision import check, judge


def generation_seed(*parts: str) -> int:
    return int(hashlib.sha256(":".join(parts).encode()).hexdigest()[:8], 16) % (2**31)


class Picker:
    # Paid calls the extra pictures (not a paragraph's first) have made, over the reel.
    extra_calls_used = 0
    _max_calls = CANDIDATES["max_vision_calls"]  # per picture, set by pick()
    _extra_slot = False

    def __init__(
        self,
        library: Library,
        client_factory: Callable[[], object],
        scr: Screen | None = None,
        dry_run: bool = False,
        log: Callable[[str], None] = print,
        cost_cap_usd: float | None = None,
    ):
        self.library = library
        self.screen = scr or screen()
        self.dry_run = dry_run
        self.log = log
        self._client_factory = client_factory
        self._client: object | None = None
        # Paid model calls this picker made (vision checks, generated illustrations):
        # cost rows (reelline/providers/costs.py), for the reel's cost.
        self.calls: list[dict] = []
        # No paid call starts once the spend plus VISION["call_ceiling_usd"] would pass this.
        self.cost_cap_usd = cost_cap_usd
        self.capped = False
        self.commons = Commons()
        try:
            self.pixabay: Pixabay | None = Pixabay(load_pixabay(), library)
        except ConfigError as exc:
            self.pixabay = None
            self.log(f"NOTE: Pixabay skipped ({exc})")
        try:
            self.pexels: Pexels | None = Pexels(load_pexels())
        except ConfigError:
            self.pexels = None  # Pexels has paused new keys; prod has none

    def spent_usd(self) -> float:
        return sum(c.get("cost_usd") or 0 for c in self.calls)

    def _before_call(self) -> None:
        if self._extra_slot and self.extra_calls_used >= CANDIDATES["extra_slots_vision_budget"]:
            raise SlotBudgetReached(
                f"the extra pictures' budget of {CANDIDATES['extra_slots_vision_budget']} vision calls is used"
            )
        cap = self.cost_cap_usd
        if cap is not None and self.spent_usd() + VISION["call_ceiling_usd"] > cap:
            raise CostCapReached(f"cost cap ${cap:.2f} reached (${self.spent_usd():.4f} spent)")

    @property
    def client(self) -> object:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    # --- evaluation ---------------------------------------------------------------

    def _store(self, record: dict) -> dict:
        return record if self.dry_run else self.library.put(record)

    def _reject_row(self, source: str, key: str, title: str, reason: str, **extra) -> Evaluation:
        """Record a candidate rejected before the vision check (never fetched again)."""
        existing = self.library.by_source_key(key)
        if existing is None:
            self._store(
                {
                    "id": key_id(key),
                    "source": source,
                    "source_key": key,
                    "status": "rejected",
                    "reason": reason,
                    **extra,
                }
            )
        return Evaluation(source, title, "rejected", [reason], extra.get("license"))

    def evaluate_reject(self, reject: Reject) -> Evaluation:
        return self._reject_row(
            reject.source,
            reject.source_key,
            reject.title,
            reject.reason,
            page_url=reject.page_url,
            license=reject.license,
        )

    def evaluate_record(
        self, record: dict, display: str, card: str | None, query: str, pinned: bool = False
    ) -> Evaluation:
        """Vision-check a stored library image against this paragraph."""
        path = self.library.path(record)
        result, cached = check(
            self.client, self.library, record["sha256"], path, display, card, query, self.calls, self._before_call
        )
        verdict = judge(result, record["source"])
        ev = Evaluation(
            record["source"],
            record.get("page_url") or record["id"],
            "pass" if verdict.passed else "fail",
            list(verdict.reasons),
            record["license"],
            record["id"],
            record,
            result,
            path,
            cached,
        )
        if verdict.intrinsic and not pinned:
            ev.record = self._store({**record, "status": "rejected", "reason": ", ".join(verdict.intrinsic)})
        elif verdict.passed and not self.dry_run and query not in record["queries"]:
            # Only queries it passed for: a failed one would make it match again.
            self.library.add_query(record["id"], query)
        return ev

    def evaluate_candidate(self, cand: Candidate, display: str, card: str | None, query: str) -> Evaluation:
        """Download (unless known), store, vision-check and record a search candidate."""
        known = self.library.by_source_key(cand.source_key)
        if known and known["status"] == "rejected":
            return Evaluation(
                cand.source,
                cand.title,
                "rejected",
                [f"rejected earlier: {known['reason']}"],
                known.get("license"),
                known["id"],
                known,
            )
        if known and known.get("path"):
            return self.evaluate_record(known, display, card, query)
        try:
            data = cand.data if cand.data is not None else self.commons.http.download(cand.download_url)
            im, ext = open_image(data)
        except SourceError as exc:
            return self._reject_row(
                cand.source, cand.source_key, cand.title, str(exc), page_url=cand.page_url, license=cand.license
            )
        problem = framing.resolution_problem(im.width, im.height, self.screen)
        if not problem and cand.mime != "image/svg+xml":
            problem = aspect_problem(im.width, im.height)
        if problem:
            return self._reject_row(
                cand.source, cand.source_key, cand.title, problem, page_url=cand.page_url, license=cand.license
            )
        sha, path = self.library.store_file(data, ext)
        same_file = self.library.get(image_id(sha))
        if same_file and same_file["status"] == "rejected":
            return Evaluation(
                cand.source,
                cand.title,
                "rejected",
                [f"rejected earlier: {same_file['reason']}"],
                same_file.get("license"),
                same_file["id"],
                same_file,
            )
        if same_file and same_file.get("path"):
            return self.evaluate_record(same_file, display, card, query)

        result, cached = check(
            self.client, self.library, sha, path, display, card, query, self.calls, self._before_call
        )
        verdict = judge(result, cand.source)
        record = self._store(
            {
                "id": image_id(sha),
                "sha256": sha,
                "path": str(path.relative_to(self.library.root)),
                "width": im.width,
                "height": im.height,
                "mime": Image.MIME.get(im.format, cand.mime),
                "source": cand.source,
                "source_key": cand.source_key,
                "page_url": cand.page_url,
                "file_url": cand.file_url,
                "license": cand.license,
                "license_url": cand.license_url,
                "author": cand.author,
                "author_url": cand.author_url,
                "attribution": cand.attribution,
                "attribution_required": cand.attribution_required,
                "editorial_only": cand.editorial_only or (result["is_logo_or_graphic"] and "logo" in query.lower()),
                "queries": [query] if verdict.passed else [],
                "tags": result["tags"],
                "vision": result,
                "is_logo_or_graphic": result["is_logo_or_graphic"],
                "subjects": cand.subjects,
                "status": "rejected" if verdict.intrinsic else "accepted",
                "reason": ", ".join(verdict.intrinsic) or None,
            }
        )
        return Evaluation(
            cand.source,
            cand.title,
            "pass" if verdict.passed else "fail",
            list(verdict.reasons),
            cand.license,
            record["id"],
            record,
            result,
            path,
            cached,
        )

    # --- search -------------------------------------------------------------------

    def gather(
        self, query: str, display: str, card: str | None, exclude: AbstractSet[str] = frozenset(), full: bool = False
    ) -> list[Evaluation]:
        """Evaluations in source and rank order (SOURCE_ORDER for the query's type), up to
        the first passing one or CANDIDATES["max_vision_calls"] paid vision calls (at most
        CANDIDATES["library_paid_calls"] of them on library images). `full` (the search
        CLI) checks up to per_source candidates from every source instead. A cost cap
        that's reached ends the list with a "cost cap" evaluation."""
        q = parse_query(query)
        evals: list[Evaluation] = []
        seen = set(exclude)  # image ids already evaluated (or excluded)

        def done() -> bool:
            if full:
                return False
            return evals[-1].status == "pass" or sum(e.paid for e in evals) >= self._max_calls

        try:
            for name in SOURCE_ORDER[q.kind]:
                if name not in SOURCES:
                    continue
                if name == "library":
                    if self._gather_library(q, display, card, seen, evals, full, done):
                        return evals
                    continue
                source = getattr(self, name, None)
                if source is None:
                    continue
                try:
                    found = source.search(query, self.screen)
                except SourceError as exc:
                    self.log(f"WARNING: {name} search for {query!r} failed, skipping it: {exc}")
                    continue
                taken = 0
                for item in found:
                    if taken >= CANDIDATES["per_source"]:
                        break
                    if isinstance(item, Reject):
                        evals.append(self.evaluate_reject(item))
                        continue
                    if q.named and not any(names_entity(item.names, e) for e in q.entities):
                        # Not recorded: the file may be right for another query.
                        evals.append(
                            Evaluation(
                                item.source,
                                item.title,
                                "rejected",
                                [f"doesn't name {' or '.join(q.entities)}"],
                                item.license,
                            )
                        )
                        continue
                    known = self.library.by_source_key(item.source_key)
                    if known and known["id"] in seen:
                        continue
                    ev = self.evaluate_candidate(item, display, card, query)
                    if ev.image_id in seen:  # same file as one already evaluated
                        continue
                    if ev.image_id:
                        seen.add(ev.image_id)
                    evals.append(ev)
                    if ev.result is not None:
                        taken += 1
                        if done():
                            return evals
        except SlotBudgetReached as exc:
            self.log(f"  {exc}; this picture keeps the previous one")
            evals.append(Evaluation("-", "(extra budget)", "rejected", [str(exc)]))
        except CostCapReached as exc:
            if not self.capped:
                self.log(f"  STOP: {exc}; no more vision calls")
            self.capped = True
            evals.append(Evaluation("-", "(cost cap)", "rejected", [str(exc)]))
        return evals

    def _gather_library(
        self,
        q: Query,
        display: str,
        card: str | None,
        seen: set[str],
        evals: list[Evaluation],
        full: bool,
        done: Callable[[], bool],
    ) -> bool:
        """Library matches into `evals`; True when the search is done."""
        taken = paid = 0
        for _, record in self.library.matches(q):
            if taken >= CANDIDATES["per_source"] or (not full and paid >= CANDIDATES["library_paid_calls"]):
                break
            if record["id"] in seen:
                continue
            seen.add(record["id"])
            # Earlier illustrations stay in the library; with the fallback off, search
            # only reuses licensed images (a lib: pin can still name one).
            if record["source"] == "generated" and not GENERATED["enabled"]:
                continue
            if not record["is_logo_or_graphic"] and aspect_problem(record["width"], record["height"]):
                continue
            ev = self.evaluate_record(record, display, card, q.text)
            evals.append(ev)
            taken += 1
            paid += ev.paid
            if done():
                return True
        return False

    def generate(self, query: str, display: str, card: str | None, seed: int) -> Evaluation:
        self.log(f"  generating an illustration for {query!r} ({conf().models.illustrator.model}, seed {seed})")
        try:
            cand = generate(self.client, query, seed, self.calls)
        except SourceError as exc:
            return Evaluation("generated", f"generated: {query}", "rejected", [str(exc)])
        return self.evaluate_candidate(cand, display, card, query)

    def pick(
        self,
        query: str,
        display: str,
        card: str | None,
        seed: int,
        exclude: AbstractSet[str] = frozenset(),
        full: bool = False,
        extra: bool = False,
    ) -> Pick:
        """`extra`: a picture after a paragraph's first: fewer vision calls, from the reel's
        extra budget, and no generated fallback (it would cost more than the rest)."""
        self._extra_slot = extra
        self._max_calls = CANDIDATES["extra_slot_max_vision_calls"] if extra else CANDIDATES["max_vision_calls"]
        calls_before = len(self.calls) if extra else 0
        try:
            return self._pick(query, display, card, seed, exclude, full, extra)
        finally:
            if extra:
                self.extra_calls_used += len(self.calls) - calls_before
            self._extra_slot = False
            self._max_calls = CANDIDATES["max_vision_calls"]

    def _pick(
        self, query: str, display: str, card: str | None, seed: int, exclude: AbstractSet[str], full: bool, extra: bool
    ) -> Pick:
        evals = self.gather(query, display, card, exclude, full)
        generated = False
        passing = [e for e in evals if e.status == "pass" and e.image_id not in exclude]
        tail = "; illustrations off"
        if not passing and GENERATED["enabled"] and not extra:
            mark = sorted(set(query_words(query)) & set(GENERATED["skip_words"]))
            if mark:
                self.log(f"  NOTE: no generated fallback for {query!r}: it asks for a real mark ({', '.join(mark)})")
                tail = f"; no generated fallback for a {mark[0]} query"
            else:
                generated, tail = True, ""
                ev = self.generate(query, display, card, seed)
                evals.append(ev)
                if ev.status == "pass" and ev.image_id not in exclude:
                    passing = [ev]
        if passing:
            best = max(passing, key=lambda e: (e.relevance, -evals.index(e)))
            return Pick(best, evals, generated, "")
        reason = no_pick_reason(evals, tail)
        self.log(f"  no image for {query!r}, idle screen: {reason}")
        return Pick(None, evals, generated, reason)
