"""The step contract: what a pipeline step is, what it passes on, and where its results are kept.

A step has a `name`, the artifacts it `needs` (and optionally `wants`), the ones it `makes`, and
`run(ctx) -> StepResult`. The runner (reelline/pipeline/runner.py) hands it a Context holding the
artifacts made so far; the chain (reelline/pipeline/chain.py) checks on load that every enabled
step's needs are made upstream.

A check is a step with `judges`: the artifacts it may reject. Its StepResult names the rejected
ones with the reasons (`rejects`), and the runner sends the work back to the step that made each
(the first step making it without needing it), with those reasons in `ctx.failures`.

Results are kept per pass in a ResultStore (`ctx.saved()`), so a rerun goes on after the last
finished pass: The host's is scripts.passes (reelline/pipeline/script.py PassesStore), keyed
"<route>/<step key>" inside a check's repair round ("first/story", "punchup/factcheck", ...) and
"<step key>" outside it ("fx", "card/write").
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol

from reelline.core.errors import StepError


class Artifact(StrEnum):
    """What steps pass on. A channel's own step may name others (plain strings)."""

    STORIES = "Stories"  # the ingested pool (the stories table)
    ASSESSMENTS = "Assessments"  # scored stories
    STORY = "Story"  # the one picked for a slot (its "text" is the source)
    BRIEF = "Brief"  # the facts sheet (markdown)
    DRAFT = "Draft"  # the plain first script (script.PassResult)
    SCRIPT = "Script"  # the styled, parsed script (script.PassResult with .script)
    DRAFT_LOCK = "DraftLock"  # draft vs script, sentence by sentence (script.draft_lock())
    FACTCHECK = "FactCheck"  # the script's claims and verdicts (factcheck.FactCheck)
    CARDS = "Cards"  # the end card's checked lines (endcard.Card)
    GRAPHICS = "Graphics"  # the fact graphics the render may show (fx.FxCheck)
    CAPTION = "Caption"  # the post's caption fields
    SCRIPT_FILE = "ScriptFile"  # the files the renderer reads (the script, its card and fx files)
    AUDIO = "Audio"  # the voice track
    VIDEO = "Video"  # the rendered reel
    QA = "QA"  # the reel's checks
    REVIEW = "Review"  # the review sent (or its decision)
    POST = "Post"  # the scheduled or published posts
    STATS = "Stats"  # the posts' numbers
    RANKING = "Ranking"  # what worked (the hook examples)


@dataclass
class StepResult:
    made: dict[str, Any] = field(default_factory=dict)
    # A check's verdict: artifact -> the reasons it fails (empty: passed).
    rejects: dict[str, list[str]] = field(default_factory=dict)
    # ok: go on; wait: nothing more to do until someone acts (a review, a menu), the run stops
    # cleanly; failed: the run stops with `detail`; skipped: nothing done (dry run, nothing due).
    status: str = "ok"
    detail: str | None = None
    note: str | None = None  # what the runner logs before sending rejected work back


class ResultStore(Protocol):
    def __contains__(self, key: str) -> bool: ...
    def get(self, key: str) -> Any: ...
    def put(self, key: str, value: Any) -> None: ...


class MemoryStore(dict[str, Any]):
    """Results for one run, kept nowhere (library use, tests)."""

    def put(self, key: str, value: Any) -> None:
        self[key] = value


class JsonFileStore(MemoryStore):
    """Results kept in a JSON file (`reelline run --input`): a rerun goes on after the last pass."""

    def __init__(self, path: Path):
        self.path = Path(path)
        super().__init__(json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {})

    def put(self, key: str, value: Any) -> None:
        self[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self, ensure_ascii=False, indent=1), encoding="utf-8")


@dataclass
class Context:
    """What a step sees: the artifacts so far, its store, the providers, the database (None:
    library mode, plain inputs only) and the run's state (`state`, free for a session's own)."""

    artifacts: dict[str, Any] = field(default_factory=dict)
    store: ResultStore = field(default_factory=MemoryStore)
    log: Callable[[str], None] = lambda m: print(m, flush=True)
    clients: Mapping[str, Any] = field(default_factory=dict)  # role -> provider (or a test fake)
    conn: Any = None
    dry_run: bool = False
    state: dict[str, Any] = field(default_factory=dict)
    # Set by the runner:
    route: str = "first"  # the repair round's name ("first", "story+punchup", "punchup")
    prefix: str = ""  # the store key prefix ("<route>/" inside a check's round)
    failures: dict[str, list[str]] = field(default_factory=dict)  # artifact -> a check's reasons
    sessions: dict[str, Callable[..., Any]] = field(default_factory=dict)  # session name -> handler(steps, ctx)

    def __getitem__(self, artifact: str) -> Any:
        try:
            return self.artifacts[artifact]
        except KeyError:
            raise StepError(f"no {artifact} in this run") from None

    def __contains__(self, artifact: str) -> bool:
        return artifact in self.artifacts

    def llm(self, role: str) -> Any:
        """The provider of a role: the one this run was given (a test's fake), else the configured one."""
        if role in self.clients:
            return self.clients[role]
        from reelline.providers import get_llm

        return get_llm(role)

    def saved(
        self, key: str, run: Callable[[], Any], to_json: Callable[[Any], Any], from_json: Callable[[Any], Any]
    ) -> Any:
        """A pass's result: the one stored by an earlier run, or run now and stored at once, so a
        retry goes on from the pass that failed."""
        key = self.prefix + key
        if key in self.store:
            self.log(f"  {key}: kept from the earlier run")
            return from_json(self.store.get(key))
        result = run()
        self.store.put(key, to_json(result))
        return result


class Step:
    """One pipeline step. Subclasses set the class attributes and run()."""

    name: ClassVar[str] = ""
    needs: ClassVar[frozenset[str]] = frozenset()
    wants: ClassVar[frozenset[str]] = frozenset()  # used when there, never required
    makes: ClassVar[frozenset[str]] = frozenset()
    judges: ClassVar[frozenset[str]] = frozenset()  # a check: what it may send back
    key: ClassVar[str | None] = None  # its store key (default: name)
    session: ClassVar[str | None] = None  # consecutive steps of one session share a handler
    sends: ClassVar[bool] = False  # sends something out (a review, a post): not in a dry run
    # A check: errors that end a repair round, keeping the last version that ran to the end.
    repair_errors: ClassVar[tuple[type[BaseException], ...]] = ()
    repair_error_key: ClassVar[str] = "repair_error"

    def __init__(self, settings: Any = None):
        self.settings = settings  # its steps.<name> section (StepConf), None outside a channel

    @property
    def store_key(self) -> str:
        return self.key or self.name

    @property
    def repair_rounds(self) -> int:
        return getattr(self.settings, "repair_rounds", 1)

    def run(self, ctx: Context) -> StepResult:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<step {self.name}>"
