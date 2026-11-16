"""The one retry policy of every provider call (models.retry in the channel config).

with_retries() runs a call up to models.retry.attempts times while it raises a transport
error (transport_errors(): the connection dropped, was refused or timed out on a read,
before or during the answer), waiting models.retry.waits_s between tries. Anything else (an
HTTP error status such as a 4xx, a refusal, an answer that fails validation) is raised
at once: the SDKs retry their own 429s and 5xxs. Each failed try goes to
failed(attempt, exc) (the adapters record it in llm_calls with the error and the try number).
After the last try it raises TransportFailure, which names the call: "punchup
(claude-sonnet-5-5) RemoteProtocolError after 3 tries: peer closed connection ...". It is not
an LLMError, so it goes up to the job as it is (reelline/pipeline/approve_bot.py run_job()
reports its message without a type name); only the proofread pass catches it (it keeps the
original text, as for any failed call).

A role with `transport_retry: false` (the image and TTS roles: a render's calls never had
these retries) makes one try and lets a dropped connection's own error through, as before.

with_status_retries() is the second, opt-in layer: an HTTP 429 / 5xx answer tried again
`retries` times, `wait_s` apart (a role's api_retries / api_retry_wait_s; Gemini's JSON calls,
whose SDK runs without retry options).

The Anthropic SDK (1.10) runs on httpx2, a fork of httpx with its own exception
classes; google-genai runs on httpx and doesn't wrap its errors (without retry options
it re-raises them as they are), so httpx's classes are its transport errors. The
Anthropic SDK wraps errors while it sends a request (APIConnectionError and its
APITimeoutError, after the SDK's own retries) but not while a stream is read: a stream
cut off mid-answer raises httpx2.RemoteProtocolError ("incomplete chunked read") from
the stream itself.
The OpenAI-compatible adapter talks httpx directly.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import cache
from typing import TypeVar

from reelline.config import conf
from reelline.providers.base import ProviderError

T = TypeVar("T")
TRANSPORT_ERROR_NAMES = ("RemoteProtocolError", "ReadError", "ReadTimeout", "ConnectError")
RETRYABLE_STATUS = (429, 500, 503, 504)
sleep = time.sleep  # tests replace it (tests/conftest.py)


class TransportFailure(ProviderError):
    """A call whose connection dropped on every try."""

    def __init__(self, purpose: str, model: str, error: BaseException, tries: int):
        self.purpose, self.model, self.error, self.tries = purpose, model, error, tries
        super().__init__(f"{purpose} ({model}) {type(error).__name__} after {tries} tries: {error}")


@cache
def transport_errors() -> tuple[type[BaseException], ...]:
    """httpx's and httpx2's dropped-connection errors, and the Anthropic SDK's connection error
    (each only when installed)."""
    import httpx

    modules = [httpx]
    try:
        import httpx2

        modules.append(httpx2)
    except ImportError:
        pass
    errors = tuple(getattr(m, name) for m in modules for name in TRANSPORT_ERROR_NAMES)
    try:
        import anthropic

        errors += (anthropic.APIConnectionError,)
    except ImportError:
        pass
    return errors


def error_text(exc: BaseException) -> str:
    """A failed try's error as llm_calls.error keeps it."""
    return f"{type(exc).__name__}: {exc}"[:500]


def with_retries[T](
    call: Callable[[], T],
    *,
    purpose: str,
    model: str,
    failed: Callable[[int, BaseException], None] | None = None,
    enabled: bool = True,
) -> tuple[T, int]:
    """(call()'s result, the try that answered, from 1). A transport error on the last
    try raises TransportFailure; any other error is raised as it is. enabled=False: one try,
    nothing caught."""
    if not enabled:
        return call(), 1
    attempts = conf().models.retry.attempts
    for attempt in range(1, attempts + 1):
        try:
            return call(), attempt
        except transport_errors() as exc:
            if failed is not None:
                failed(attempt, exc)
            if attempt == attempts:
                raise TransportFailure(purpose, model, exc, attempts) from exc
            wait = conf().models.retry.waits_s[attempt - 1]
            print(
                f"  {purpose} ({model}): {type(exc).__name__} on try {attempt} of {attempts} ({exc}); "
                f"retrying in {wait:g}s",
                flush=True,
            )
            sleep(wait)
    raise AssertionError("models.retry.attempts must be at least 1")


def with_status_retries[T](
    call: Callable[[], T],
    *,
    status_of: Callable[[BaseException], int | None],
    error: type[BaseException],
    retries: int,
    wait_s: float,
    label: str,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """call(), tried again up to `retries` times on an `error` whose status_of() is 429 / 5xx;
    any other `error` (or the last) is raised as it is."""
    pause = sleep or globals()["sleep"]
    for attempt in range(retries + 1):
        try:
            return call()
        except error as exc:
            code = status_of(exc)
            if code in RETRYABLE_STATUS and attempt < retries:
                print(f"  {label}: {code}, retrying in {wait_s:g}s", flush=True)
                pause(wait_s)
                continue
            raise
    raise AssertionError("unreachable")
