"""The error hierarchy (reelline/core/errors.py): one root, and nothing that was caught is caught less."""

import importlib
import inspect
import pkgutil

import pytest
import reelline
from reelline.config import ConfigError as ConfigErrorFromConfig
from reelline.core import errors as E
from reelline.notify.base import NotifyError as NotifyErrorFromNotify
from reelline.pipeline.factcheck import FactCheckError
from reelline.pipeline.publish import PublishError as PublishErrorFromPipeline
from reelline.pipeline.qa import QaError
from reelline.pipeline.step import StepError as StepErrorFromStep
from reelline.providers.base import LLMError, ProviderError, RefusalError, TTSError
from reelline.providers.retry import TransportFailure
from reelline.publish.base import PublisherError, QueueFull
from reelline.review.base import ReviewError as ReviewErrorFromReview


def test_the_named_families_hang_off_one_root():
    for cls in (E.ConfigError, E.ProviderError, E.StepError, E.CheckFailed, E.PublishError, E.ReviewError):
        assert issubclass(cls, E.ReellineError)
    assert issubclass(E.ReviewError, E.NotifyError)


def test_the_old_import_paths_still_name_the_same_classes():
    assert ConfigErrorFromConfig is E.ConfigError
    assert NotifyErrorFromNotify is E.NotifyError
    assert ReviewErrorFromReview is E.ReviewError
    assert StepErrorFromStep is E.StepError
    assert PublishErrorFromPipeline is E.PublishError
    assert ProviderError is E.ProviderError


def test_providers_checks_and_publishers_sit_where_callers_catch_them():
    assert all(issubclass(c, E.ProviderError) for c in (LLMError, RefusalError, TTSError, TransportFailure))
    assert issubclass(FactCheckError, E.CheckFailed) and issubclass(QaError, E.CheckFailed)
    assert issubclass(PublisherError, E.PublishError) and issubclass(QueueFull, PublisherError)


def engine_errors():
    found = {}
    for info in pkgutil.walk_packages(reelline.__path__, "reelline."):
        module = importlib.import_module(info.name)
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and issubclass(obj, Exception) and obj.__module__ == info.name:
                found[f"{info.name}.{name}"] = obj
    return found


def test_every_error_the_engine_defines_is_a_reelline_error_and_keeps_its_builtin_base():
    found = engine_errors()
    assert len(found) > 50
    stray = [n for n, c in found.items() if not issubclass(c, E.ReellineError)]
    assert stray == []
    # a builtin base is kept: `except RuntimeError` / `except ValueError` around engine code still catches them
    only_root = [
        n for n, c in found.items() if not issubclass(c, (RuntimeError, ValueError)) and c is not E.ReellineError
    ]
    assert sorted(only_root) == ["reelline.pipeline.fetch.FetchError", "reelline.pipeline.ingest.base.SourceError"]


@pytest.mark.parametrize("cls", [E.ConfigError, E.StepError, E.PublishError, E.NotifyError, E.ProviderError])
def test_the_old_runtime_error_catch_still_works(cls):
    with pytest.raises(RuntimeError):
        raise cls("x")
