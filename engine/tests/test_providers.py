"""reelline/providers/: the OpenAI-compatible adapter against a fake HTTP transport, the Google adapter
against a fake client (request shape, usage, both retry layers, error mapping, cost rows), the cost
module and ledger, and the fakes steps test with. The Anthropic adapter's tests are in
tests/test_script_chain.py ("The client", "Dropped connections"); the Google JSON path's through the
script chain and the pick flow too."""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from reelline.config import conf
from reelline.config.schema import ModelRole
from reelline.pipeline import db
from reelline.providers import (
    CostLedger,
    LLMError,
    LLMUsage,
    RefusalError,
    TransportFailure,
    cost_usd,
    costs,
    get_llm,
    get_tts,
    get_vision,
)
from reelline.providers.fakes import FakeLLM, FakeTTS, silent_wav
from reelline.providers.openai import OpenAIProvider, usage_of

GPT = "gpt-test"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "t.db")


@pytest.fixture
def gpt(monkeypatch):
    """The stylist role on an OpenAI-compatible endpoint, priced."""
    role = ModelRole(
        provider="openai",
        model=GPT,
        max_tokens=4000,
        temperature=0.2,
        seed=3,
        effort="low",
        refusal_fallback="gpt-big",
        api_retries=1,
        api_retry_wait_s=7.0,
    )
    monkeypatch.setattr(conf().models, "stylist", role)
    monkeypatch.setitem(conf().models.prices, GPT, {"input": 1.0, "output": 4.0})
    monkeypatch.setitem(conf().models.prices, "gpt-big", {"input": 10.0, "output": 40.0})
    return role


class Endpoint:
    """A fake /v1/chat/completions: answers in order (a dict body, an int status, or an exception)."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": {"message": f"status {answer}"}})
        return httpx.Response(200, json=answer)

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url="https://llm.test/v1/", transport=httpx.MockTransport(self), headers={"Authorization": "Bearer k"}
        )

    def body(self, n=0) -> dict:
        return json.loads(self.requests[n].content)


def chat(text="hello", *, refusal=None, finish="stop", model=GPT, tokens=(1000, 600, 200)):
    return {
        "model": model,
        "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text, "refusal": refusal}}],
        "usage": {
            "prompt_tokens": tokens[0],
            "completion_tokens": tokens[1],
            "completion_tokens_details": {"reasoning_tokens": tokens[2]},
        },
    }


# ---- OpenAI-compatible --------------------------------------------------------------------------


def test_openai_request_shape_usage_and_cost(gpt, conn):
    ep = Endpoint(chat("hi there"))
    llm = get_llm("stylist", ep.client())
    assert isinstance(llm, OpenAIProvider)
    reply = llm.complete(
        [{"role": "user", "content": "go"}],
        purpose="punchup",
        prompt_version=3,
        system="be brief",
        schema=SCHEMA,
        ledger=CostLedger(conn, script_id=None),
    )
    request = ep.requests[0]
    assert request.url == "https://llm.test/v1/chat/completions" and request.headers["Authorization"] == "Bearer k"
    assert ep.body() == {
        "model": GPT,
        "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "go"}],
        "max_completion_tokens": 4000,
        "temperature": 0.2,
        "seed": 3,
        "reasoning_effort": "low",
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "punchup", "schema": SCHEMA, "strict": True},
        },
    }
    # 600 completion tokens of which 200 reasoning: output 400, thinking 200, both billed as output.
    assert reply.text == "hi there" and reply.usage == LLMUsage(1000, 400, 200) and reply.model == GPT
    assert reply.cost_usd == pytest.approx((1000 * 1 + 600 * 4) / 1e6)
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert (
        row["purpose"],
        row["model"],
        row["prompt_version"],
        row["input_tokens"],
        row["output_tokens"],
        row["thinking_tokens"],
        row["attempt"],
        row["error"],
    ) == ("punchup", GPT, 3, 1000, 400, 200, 1, None)
    assert row["cost_usd"] == pytest.approx(reply.cost_usd) and reply.call_id == row["id"]


def test_openai_follow_up_and_json(gpt):
    ep = Endpoint(chat('{"ok": true}'), chat('{"ok": false}'), chat("[1]"), chat("nope"))
    llm = get_llm("stylist", ep.client())
    messages = [{"role": "user", "content": "q"}]
    first = llm.complete(messages, purpose="p", prompt_version=1)
    assert llm.follow_up(messages, first, "again") == [
        *messages,
        {"role": "assistant", "content": '{"ok": true}'},
        {"role": "user", "content": "again"},
    ]
    assert llm.complete_json("q", SCHEMA, purpose="p", prompt_version=1).data == {"ok": False}
    with pytest.raises(LLMError, match="list, not an object"):
        llm.complete_json("q", SCHEMA, purpose="p", prompt_version=1)
    with pytest.raises(LLMError, match="invalid JSON"):
        llm.complete_json("q", SCHEMA, purpose="p", prompt_version=1)


def test_openai_refusal_goes_to_the_fallback_then_fails(gpt, conn):
    ep = Endpoint(chat(None, refusal="no"), chat("ok", model="gpt-big"))
    reply = get_llm("stylist", ep.client()).complete("q", purpose="punchup", prompt_version=1, ledger=CostLedger(conn))
    assert reply.text == "ok" and reply.model == "gpt-big" and ep.body(1)["model"] == "gpt-big"
    assert [tuple(r) for r in conn.execute("SELECT purpose, model FROM llm_calls ORDER BY id")] == [
        ("punchup", GPT),
        ("punchup_fallback", "gpt-big"),
    ]
    ep = Endpoint(chat(None, refusal="no"), chat(None, refusal="still no", model="gpt-big"))
    with pytest.raises(RefusalError, match="refused by gpt-test and gpt-big: still no"):
        get_llm("stylist", ep.client()).complete("q", purpose="punchup", prompt_version=1)
    with pytest.raises(LLMError, match="max_tokens"):
        get_llm("stylist", Endpoint(chat("cut", finish="length")).client()).complete("q", purpose="x", prompt_version=1)
    with pytest.raises(LLMError, match="no text"):
        get_llm("stylist", Endpoint(chat("  ")).client()).complete("q", purpose="x", prompt_version=1)


def test_openai_http_errors(gpt, retry_waits):
    ep = Endpoint(400)
    with pytest.raises(LLMError, match="HTTP 400: status 400") as failure:
        get_llm("stylist", ep.client()).complete("q", purpose="punchup", prompt_version=1)
    assert len(ep.requests) == 1 and retry_waits == [] and "Bearer" not in str(failure.value)
    ep = Endpoint(429, chat("ok"))  # api_retries: 1, 7 s apart
    assert get_llm("stylist", ep.client()).complete("q", purpose="punchup", prompt_version=1).text == "ok"
    assert len(ep.requests) == 2 and retry_waits == [7.0]
    with pytest.raises(LLMError, match="HTTP 503"):
        get_llm("stylist", Endpoint(503, 503).client()).complete("q", purpose="punchup", prompt_version=1)


def test_openai_dropped_connection_is_retried_and_recorded(gpt, conn, retry_waits):
    drop = httpx.ReadTimeout("timed out")
    ep = Endpoint(drop, chat("ok"))
    reply = get_llm("stylist", ep.client()).complete("q", purpose="punchup", prompt_version=1, ledger=CostLedger(conn))
    assert reply.text == "ok" and retry_waits == [5]
    assert [tuple(r) for r in conn.execute("SELECT attempt, error, input_tokens FROM llm_calls ORDER BY id")] == [
        (1, "ReadTimeout: timed out", None),
        (2, None, 1000),
    ]
    with pytest.raises(TransportFailure, match=r"^punchup \(gpt-test\) ReadTimeout after 3 tries: timed out$"):
        get_llm("stylist", Endpoint(drop, drop, drop).client()).complete("q", purpose="punchup", prompt_version=1)


def test_openai_vision_sends_a_data_url(gpt, monkeypatch):
    monkeypatch.setattr(conf().models, "vision", conf().models.stylist)
    ep = Endpoint(chat('{"ok": true}'))
    rows = []
    reply = get_vision("vision", ep.client()).look(
        b"\xff\xd8jpeg", "is it ok?", SCHEMA, purpose="image_vision", prompt_version=4, ledger=CostLedger(rows=rows)
    )
    image, text = ep.body()["messages"][0]["content"]
    assert image == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8jpeg").decode()},
    }
    assert text == {"type": "text", "text": "is it ok?"} and reply.data == {"ok": True}
    assert rows == [reply.row] and rows[0]["usage"] == {"input": 1000, "output": 400, "thinking": 200}


def test_openai_connect_reads_the_key_from_the_env_file(tmp_path, monkeypatch):
    from reelline.providers import openai

    env = tmp_path / "x.env"
    env.write_text("OPENAI_API_KEY=sk-file\n")
    monkeypatch.setenv("REELLINE__STORAGE__ENV_FILE", str(env))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell")
    monkeypatch.setattr(conf().storage, "env_file", env)
    settings = conf().models.providers.openai.model_copy(update={"base_url": "http://localhost:11434/v1"})
    client = openai.connect(settings)
    assert client.headers["Authorization"] == "Bearer sk-file" and str(client.base_url) == "http://localhost:11434/v1/"
    env.write_text("")
    assert "Authorization" not in openai.connect(settings).headers  # a local endpoint without a key


def test_openai_usage_without_details():
    assert usage_of({"usage": {"prompt_tokens": 5, "completion_tokens": 7}}) == LLMUsage(5, 7, 0)
    assert usage_of({}) == LLMUsage()


# ---- Google ---------------------------------------------------------------------------------------


class Gemini:
    """models.generate_content / interactions.create: answers in order (an exception is raised)."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []
        self.models = self.interactions = self

    def generate_content(self, **kw):
        return self._next(kw)

    def create(self, **kw):
        return self._next(kw)

    def _next(self, kw):
        self.requests.append(kw)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def gemini_answer(text, tokens=(300, 40, 10)):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=tokens[0], candidates_token_count=tokens[1], thoughts_token_count=tokens[2]
        ),
    )


def test_google_json_request_and_status_retries(conn, retry_waits):
    from google.genai import errors, types

    busy = errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
    client = Gemini(busy, gemini_answer('{"ok": true}'))
    reply = get_llm("checker", client).complete_json(
        "p", SCHEMA, purpose="factcheck", prompt_version=5, ledger=CostLedger(conn, script_id=None), items=2
    )
    config = client.requests[0]["config"]
    assert client.requests[0]["model"] == "gemini-3.8-flash" and client.requests[0]["contents"] == "p"
    assert (
        config.temperature,
        config.seed,
        config.response_mime_type,
        config.response_json_schema,
        config.thinking_config,
        config.automatic_function_calling.disable,
    ) == (0.0, 0, "application/json", SCHEMA, types.ThinkingConfig(thinking_level="low"), True)
    assert config.max_output_tokens is None and retry_waits == [10.0]
    assert reply.data == {"ok": True} and reply.usage == LLMUsage(300, 40, 10)
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert (row["items"], row["attempt"], row["cost_usd"]) == (2, 1, pytest.approx((300 * 0.75 + 50 * 3.75) / 1e6))
    client = Gemini(busy, busy, busy)
    with pytest.raises(LLMError, match="Gemini factcheck call failed: 503"):
        get_llm("checker", client).complete_json("p", SCHEMA, purpose="factcheck", prompt_version=5)
    assert len(client.requests) == 3


def test_google_text_completion_turns(conn):
    client = Gemini(gemini_answer("  answer  "))
    turns = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
    reply = get_llm("briefer", client).complete(turns, purpose="x", prompt_version=1, system="sys")
    contents, config = client.requests[0]["contents"], client.requests[0]["config"]
    assert [(c.role, c.parts[0].text) for c in contents] == [("user", "a"), ("model", "b"), ("user", "c")]
    assert config.system_instruction == "sys" and config.response_mime_type is None and reply.text == "answer"


def test_google_look_records_only_a_parsed_answer(monkeypatch):
    monkeypatch.setattr(conf().models.vision, "media_resolution", "MEDIA_RESOLUTION_MEDIUM")
    rows = []
    client = Gemini(gemini_answer('{"ok": true}', (500, 30, 0)), gemini_answer("not json"))
    vision = get_vision("vision", client)
    reply = vision.look(
        b"jpeg", "prompt", SCHEMA, purpose="image_vision", prompt_version=4, ledger=CostLedger(rows=rows)
    )
    part, prompt = client.requests[0]["contents"]
    assert prompt == "prompt" and part.inline_data.data == b"jpeg" and part.media_resolution is not None
    assert reply.data == {"ok": True} and rows[0]["cost_usd"] == pytest.approx((500 * 0.75 + 30 * 3.75) / 1e6)
    with pytest.raises(LLMError):
        vision.look(b"jpeg", "prompt", SCHEMA, purpose="image_vision", prompt_version=4, ledger=CostLedger(rows=rows))
    assert len(rows) == 1
    qa = get_vision("qa_vision", Gemini(gemini_answer("{}")))
    qa.look(b"jpeg", "p", SCHEMA, purpose="qa_vision", prompt_version=2)
    assert qa.client.requests[0]["contents"][0].media_resolution is None  # the API's default


def test_google_transport_drop_is_not_retried_for_a_render_role(retry_waits):
    drop = httpx.ReadTimeout("slow")
    with pytest.raises(httpx.ReadTimeout):  # as before: the render's calls make one try
        get_vision("vision", Gemini(drop)).look(b"j", "p", SCHEMA, purpose="image_vision", prompt_version=4)
    assert retry_waits == []


def test_google_tts_and_image_generation(monkeypatch):
    from reelline.media import tts

    wav = silent_wav()
    client = Gemini(
        SimpleNamespace(
            output_audio=SimpleNamespace(data=base64.b64encode(wav).decode()),
            usage=SimpleNamespace(total_input_tokens=12, total_output_tokens=340),
        ),
        SimpleNamespace(output_audio=SimpleNamespace(data=base64.b64encode(b"MP3!").decode()), usage=None),
    )
    speech = tts.synthesize(client, "hello", "Voice", "snarky")
    assert (speech.wav, speech.input_tokens, speech.output_tokens) == (wav, 12, 340)
    kw = client.requests[0]
    assert kw["model"] == "gemini-3.8-flash-tts" and kw["generation_config"] == {"speech_config": [{"voice": "Voice"}]}
    assert kw["input"][0]["content"][0]["annotations"] == [{"type": "speech_metadata", "style": "snarky"}]
    with pytest.raises(tts.TTSError, match="isn't WAV"):
        tts.synthesize(client, "x", "Voice", "s")
    assert tts.cost_usd(1_000_000, 0) == 0.5 and tts.call_row(10, 20)["model"] == "gemini-3.8-flash-tts"

    rows = []
    image = SimpleNamespace(
        output_image=SimpleNamespace(data=base64.b64encode(b"JPEG").decode()),
        usage=SimpleNamespace(
            total_input_tokens=60, total_output_tokens=1200, total_thought_tokens=0, output_tokens_by_modality=None
        ),
    )
    client = Gemini(image)
    monkeypatch.setattr(conf().models.illustrator, "aspect_ratio", "21:9")
    out = get_llm("illustrator", client).generate("a cat", seed=4, ledger=CostLedger(rows=rows))
    assert out.data == b"JPEG" and client.requests[0]["response_format"]["aspect_ratio"] == "21:9"
    assert client.requests[0]["generation_config"] == {"seed": 4}
    assert rows[0]["purpose"] == "image_generate_upper_bound" and rows[0]["cost_usd"] == pytest.approx(
        (60 * 0.5 + 1200 * 60) / 1e6
    )


# ---- costs -----------------------------------------------------------------------------------------


def test_cost_formulas():
    assert cost_usd("claude-sonnet-5-5", LLMUsage(1000, 200, 300)) == pytest.approx((1000 * 2 + 500 * 10) / 1e6)
    assert cost_usd("gemini-3.8-flash", LLMUsage(None, None, None)) == 0
    assert cost_usd("gemini-3.1-flash-image", LLMUsage(67, 1497, 10, image=1120)) == pytest.approx(
        (67 * 0.5 + 1120 * 60 + (377 + 10) * 3) / 1e6
    )
    assert cost_usd("nobody", LLMUsage(1, 1, 1)) is None
    assert LLMUsage(1, None, 2) + LLMUsage(None, None, 3) == LLMUsage(1, None, 5)


def test_ledger_rows_go_to_a_live_recorder(conn):
    seen = []
    rows = []
    with costs.recording(lambda row: seen.append(row) or row.update(recorded_id=7)):
        CostLedger(rows=rows).answered("tts", "gemini-3.8-flash-tts", 0, LLMUsage(1, 2, 0), cost=0.1)
    assert rows == seen and rows[0]["recorded_id"] == 7
    assert set(rows[0]) == {"purpose", "model", "prompt_version", "usage", "cost_usd", "at", "recorded_id"}
    CostLedger().answered("x", "m", 1, LLMUsage(1, 1, 0))  # nowhere: nothing to do
    CostLedger(rows=[]).failed("x", "m", 1, "E: e", 1)  # a render's try: no row
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0


# ---- fakes -----------------------------------------------------------------------------------------


def test_fake_llm_runs_a_step(conn):
    from reelline.pipeline.factcheck import fact_check

    report = {"final": [{"text": "The grant is 20,000 GEL.", "origin": "draft"}]}
    fake = FakeLLM(
        [
            {
                "claims": [
                    {"sentence": 1, "claim": "grant", "verdict": "supported", "evidence": "20,000 GEL", "note": None}
                ],
                "inserted": [],
            }
        ]
    )
    fc = fact_check(conn, fake, "A 20,000 GEL grant.", "brief", report)
    assert fc.claims[0]["evidence_found"] is True and fc.cost_usd == pytest.approx((100 * 0.75 + 50 * 3.75) / 1e6)
    assert fake.requests[0]["role"] == "checker" and fake.requests[0]["method"] == "complete_json"
    assert conn.execute("SELECT purpose, model FROM llm_calls").fetchone()[:] == ("factcheck", "gemini-3.8-flash")


def test_fake_llm_serves_several_roles_and_fake_tts(monkeypatch):
    from reelline.media import tts
    from reelline.pipeline.proofread import proofread_script

    monkeypatch.setattr(conf().steps.proofread, "enabled", True)
    fake = FakeLLM(["One two."], role="writer")
    pr = proofread_script(fake, "One two.", log=lambda m: None)
    assert fake.requests[0]["role"] == "proofreader" and pr.text == "One two."
    with pytest.raises(AssertionError, match="no reply left"):
        fake.for_role("caption").complete("q", purpose="caption", prompt_version=1)
    voice = FakeTTS(tokens=(5, 6))
    assert get_tts("tts", voice) is not voice and tts.synthesize(voice, "a", "V", "s").output_tokens == 6
    assert voice.requests == [{"text": "a", "voice": "V", "style": "s"}]
