"""What a model provider is to the engine: the protocols, the reply and usage types, the errors.

Steps never see a vendor SDK: they ask the registry (reelline/providers/registry.py) for the
provider of a role (get_llm("writer"), get_tts(), get_vision("vision"), ...) and call it.

  LLMProvider     complete() (text; messages or one prompt, a system prompt, optionally a JSON
                  schema the answer follows; multi-turn through follow_up()) and complete_json()
                  (one prompt, a JSON schema, the parsed object)
  VisionChecker   look(): one image and a prompt, a JSON schema, the parsed object
  ImageGenerator  generate(): a prompt -> image bytes
  TTSProvider     synthesize(): text, a voice, a style -> WAV bytes

Every call goes through one retry policy (reelline/providers/retry.py) and one cost module
(reelline/providers/costs.py: the price table in config, CostLedger for the llm_calls rows).

Errors: ProviderError, the base of every provider failure.
  LLMError         a call that failed (an HTTP error answer), was refused or came back unusable
    RefusalError   refused (by the fallback model too, when there is one)
  TTSError         no usable audio (also ffmpeg failing on a clip: reelline/media/tts.py)
  TransportFailure the connection dropped on every try (reelline/providers/retry.py). Not an LLMError:
                   a step's `except LLMError` lets it go up to the job (the proofread pass catches it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from reelline.core.errors import ProviderError

if TYPE_CHECKING:
    from reelline.providers.costs import CostLedger


class LLMError(ProviderError):
    """An LLM call that failed, was refused, or came back unusable."""


class RefusalError(LLMError):
    """An LLM call the model (and its refusal fallback) refused."""


class TTSError(ProviderError):
    """TTS returned something that isn't usable audio (or ffmpeg failed on a clip)."""


@dataclass(frozen=True)
class LLMUsage:
    """Tokens of one call. output excludes thinking (both are billed as output); image: the output
    tokens that are image (priced at the model's output_image rate), None when not known. A field
    is None when the provider didn't report it (llm_calls keeps NULL)."""

    input: int | None = None
    output: int | None = None
    thinking: int | None = None
    image: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """{"input", "output", "thinking"}, as reelline/pipeline/db.py record_llm_call() takes it."""
        return {"input": self.input, "output": self.output, "thinking": self.thinking}

    def __add__(self, other: LLMUsage) -> LLMUsage:
        def add(a: int | None, b: int | None) -> int | None:
            return b if a is None else a if b is None else a + b

        return LLMUsage(
            add(self.input, other.input),
            add(self.output, other.output),
            add(self.thinking, other.thinking),
            add(self.image, other.image),
        )


@dataclass(frozen=True)
class LLMReply:
    text: str
    usage: LLMUsage
    model: str  # the model that answered
    cost_usd: float | None
    call_id: int | None  # its llm_calls row, when recorded
    data: Any = None  # the parsed JSON (complete_json(), look())
    content: Any = None  # the provider's own assistant turn, replayed by follow_up()
    row: dict[str, Any] | None = None  # its cost row, when recorded away from the database


@dataclass(frozen=True)
class Speech:
    wav: bytes
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime: str
    model: str


@runtime_checkable
class Provider(Protocol):
    """Common to every adapter: the role it serves and its settings (config `models.<role>`)."""

    role: str
    spec: Any

    def for_role(self, role: str) -> Provider:
        """The same connection serving another role (or a fresh provider when that role is
        configured on another provider)."""
        ...


@runtime_checkable
class LLMProvider(Provider, Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]] | str,
        *,
        purpose: str,
        prompt_version: int,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        ledger: CostLedger | None = None,
        items: int = 1,
    ) -> LLMReply:
        """The answer's text (thinking left out). `schema`: the answer is JSON following it (not
        parsed here: the caller validates and may follow up). `ledger` (CostLedger) records
        every try in llm_calls. Raises LLMError / RefusalError / TransportFailure."""
        ...

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        purpose: str,
        prompt_version: int,
        ledger: CostLedger | None = None,
        items: int = 1,
    ) -> LLMReply:
        """One prompt, the answer parsed (reply.data, always a dict); LLMError when it isn't."""
        ...

    def follow_up(self, messages: list[dict[str, Any]], reply: LLMReply, user_text: str) -> list[dict[str, Any]]:
        """The conversation so far, the reply as the provider gave it, and a new user turn."""
        ...


@runtime_checkable
class VisionChecker(Provider, Protocol):
    def look(
        self,
        image: bytes,
        prompt: str,
        schema: dict[str, Any],
        *,
        purpose: str,
        prompt_version: int,
        mime: str = "image/jpeg",
        ledger: CostLedger | None = None,
    ) -> LLMReply:
        """The model's JSON verdict on one image (reply.data); LLMError on a failed call or
        unparsable JSON. Google's adapter records the cost only once the answer parsed (as
        it always did)."""
        ...


@runtime_checkable
class ImageGenerator(Provider, Protocol):
    def generate(
        self,
        prompt: str,
        *,
        seed: int,
        purpose: str = "image_generate",
        prompt_version: int = 1,
        ledger: CostLedger | None = None,
    ) -> GeneratedImage | None:
        """One image, None when the answer has none (its cost is recorded either way); LLMError
        when the call fails."""
        ...


@runtime_checkable
class TTSProvider(Provider, Protocol):
    def synthesize(self, text: str, *, voice: str, style: str) -> Speech:
        """One clip as WAV; TTSError when the answer has no usable audio."""
        ...


def follow_up(messages: list[dict[str, Any]], reply: LLMReply, user_text: str) -> list[dict[str, Any]]:
    """The append-only follow-up every adapter uses: the reply's own content (an Anthropic reply's
    thinking blocks included, unchanged) as the assistant turn, then the user's."""
    content = reply.content if reply.content is not None else reply.text
    return [*messages, {"role": "assistant", "content": content}, {"role": "user", "content": user_text}]


class Adapter:
    """What every vendor adapter shares: (role, its settings, the connection) and for_role()."""

    kind = ""

    def __init__(self, role: str, spec: Any, client: Any):
        self.role, self.spec, self.client = role, spec, client

    def for_role(self, role: str) -> Provider:
        from reelline.config import conf
        from reelline.providers import registry

        s = registry.spec(role)
        if getattr(conf().models.providers, s.provider).type == self.kind:
            return type(self)(role, s, self.client)
        return registry.provider(role)

    def follow_up(self, messages: list[dict[str, Any]], reply: LLMReply, user_text: str) -> list[dict[str, Any]]:
        return follow_up(messages, reply, user_text)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.role}: {self.spec.model})"


def as_messages(messages: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": messages}] if isinstance(messages, str) else messages
