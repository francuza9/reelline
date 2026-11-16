"""The chain: the configured steps, in order, checked on load.

`pipeline: [..]` in the channel's config.yaml gives the order; steps.<name>.enabled: false leaves a
step out; steps.<name>.use: "module:Class" runs another class in its place, and names the class of a
step only the channel has (a module under the channel's plugins/ is importable as plugins.<name>).
Load fails (ChainError) on an unknown step, a class that isn't a Step, or an enabled step needing an
artifact no enabled step before it makes; the message names both steps.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from reelline.core.errors import ReellineError
from reelline.pipeline.step import Step

if TYPE_CHECKING:
    from reelline.config.schema import Config

BUILTIN = {
    "ingest": "reelline.steps.ingest:Ingest",
    "assess": "reelline.steps.assess:Assess",
    "pick": "reelline.steps.pick:Pick",
    "brief": "reelline.steps.brief:Brief",
    "write": "reelline.steps.write:Write",
    "style": "reelline.steps.style:Style",
    "proofread": "reelline.steps.proofread:Proofread",
    "factcheck": "reelline.steps.factcheck:Factcheck",
    "cards": "reelline.steps.cards:Cards",
    "fx": "reelline.steps.fx:Fx",
    "caption": "reelline.steps.caption:Caption",
    "save": "reelline.steps.save:Save",
    "voice": "reelline.steps.voice:Voice",
    "render": "reelline.steps.render:Render",
    "qa": "reelline.steps.qa:Qa",
    "review": "reelline.steps.review:Review",
    "publish": "reelline.steps.publish:Publish",
    "stats": "reelline.steps.stats:Stats",
    "rank": "reelline.steps.rank:Rank",
}


class ChainError(ReellineError, ValueError):
    """A chain that can't run: the message says which step and why."""


def load_class(path: str, channel_dir: Path | None = None) -> type[Step]:
    module_name, _, attr = path.partition(":")
    if not module_name or not attr:
        raise ChainError(f'use: {path!r} is not "module:Class"')
    if channel_dir is not None and str(channel_dir) not in sys.path:
        sys.path.append(str(channel_dir))  # plugins/<x>.py is importable as plugins.<x>
    try:
        cls = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ChainError(f"use: {path!r} can't be loaded: {exc}") from None
    if not (isinstance(cls, type) and issubclass(cls, Step)):
        raise ChainError(f"use: {path!r} is not a reelline.pipeline.step.Step")
    return cls


@dataclass
class Chain:
    steps: list[Step]  # the enabled ones, in order
    disabled: list[str]  # named in the pipeline but off

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.steps]

    def has(self, name: str) -> bool:
        return name in self.names

    def get(self, name: str) -> Step:
        for s in self.steps:
            if s.name == name:
                return s
        raise ChainError(f"no enabled step {name!r} in the chain ({', '.join(self.names)})")

    def slice(self, start: str | None = None, stop: str | None = None) -> list[Step]:
        """The steps from `start` through `stop` (both included; None: the ends)."""
        a = self.names.index(self.get(start).name) if start else 0
        b = self.names.index(self.get(stop).name) if stop else len(self.steps) - 1
        if a > b:
            raise ChainError(f"--from {start} comes after --to {stop}")
        return self.steps[a : b + 1]

    def between(self, after: str, until: str) -> list[Step]:
        """The steps after the one making artifact `after`, through the one making `until`."""
        made = [i for i, s in enumerate(self.steps) if after in s.makes]
        end = [i for i, s in enumerate(self.steps) if until in s.makes]
        if not end:
            raise ChainError(f"no enabled step makes {until}")
        return self.steps[(made[0] + 1 if made else 0) : end[-1] + 1]


def check(
    steps: list[Step],
    available: set[str] | frozenset[str] = frozenset(),
    all_steps: dict[str, Step] | None = None,
    order: list[str] | None = None,
) -> None:
    """ChainError when a step needs what no step before it makes. all_steps/order (every step
    named in the pipeline, enabled or not): the message says which step would make it."""
    made = set(available)
    for i, step in enumerate(steps):
        missing = sorted(step.needs - made)
        if missing:
            art = missing[0]
            why = "no step in the pipeline makes it"
            for name in order or []:
                other = (all_steps or {}).get(name)
                if other is not None and art in other.makes and art not in other.needs and name != step.name:
                    if name not in [s.name for s in steps]:
                        why = f"'{name}' makes it, but '{name}' is disabled"
                    elif [s.name for s in steps].index(name) > i:
                        why = f"'{name}' makes it, but runs after '{step.name}'"
                    break
            raise ChainError(f"step '{step.name}' needs {art}, which nothing upstream makes: {why}")
        made |= step.makes


def load_chain(config: Config | None = None, *, channel_dir: Path | None = None) -> Chain:
    """The channel's chain (config: conf() by default), validated."""
    if config is None:
        from reelline.config import conf

        config = conf()
    channel_dir = channel_dir or getattr(config.channel, "dir", None)
    order = list(config.pipeline)
    if len(set(order)) != len(order):
        raise ChainError(f"pipeline names a step twice: {order}")
    built: dict[str, Step] = {}
    enabled: list[Step] = []
    disabled: list[str] = []
    for name in order:
        settings = config.steps.get(name)
        use = settings.use if settings is not None else None
        path = use or BUILTIN.get(name)
        if path is None:
            raise ChainError(f"step '{name}' is not built in: give it a class (steps.{name}.use: \"module:Class\")")
        cls = load_class(path, channel_dir)
        if use:
            cls = type(cls.__name__, (cls,), {"name": name})  # the configured name, whatever the class says
        step = cls(settings)
        built[name] = step
        if settings is None or settings.enabled:
            enabled.append(step)
        else:
            disabled.append(name)
    check(enabled, all_steps=built, order=order)
    return Chain(enabled, disabled)
