"""The error hierarchy: everything the engine raises on purpose is a ReellineError.

    ReellineError
      ConfigError      a config or env file that doesn't load, validate or is incomplete
      ProviderError    a model provider's call that failed (LLMError, RefusalError, TTSError, TransportFailure)
      StepError        a step that can't run as asked, or a chain run that can't go on
      CheckFailed      a check's verdict was no (fact check, QA)
      PublishError     a publish step that can't go on (PublisherError: a platform that refused)
      NotifyError      a message that failed to send
        ReviewError    a review channel that failed to send or receive

Each keeps the builtin it always had (RuntimeError or ValueError) as a second base, so an
`except RuntimeError` or `except ValueError` around engine code catches what it always caught.
`except ReellineError` is the new catch-all for the engine's own errors.
"""

from __future__ import annotations


class ReellineError(Exception):
    """Base of every error the engine raises on purpose."""


class ConfigError(ReellineError, RuntimeError):
    """A config that doesn't load or validate, or a missing or incomplete env file."""


class ProviderError(ReellineError, RuntimeError):
    """A model provider's call that failed."""


class StepError(ReellineError, RuntimeError):
    """A step that can't run as asked (a missing input, a mode it doesn't have)."""


class CheckFailed(ReellineError, RuntimeError):
    """A check whose verdict was no."""


class PublishError(ReellineError, RuntimeError):
    """A publish step that can't go on (the message says why)."""


class NotifyError(ReellineError, RuntimeError):
    """A send that failed (network, HTTP, the channel's own error). The message never holds a secret."""


class ReviewError(NotifyError):
    """A review channel that failed to send or receive."""
