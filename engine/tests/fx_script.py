"""The script chain's fakes and fixtures, shared by the tests that need a scripted pick: a fake Anthropic client
(recording every request), a fake Gemini, and a router that answers each pass of the chain. The script texts follow
the channel's language (fixtures/script/<language>/; Georgian, else English)."""

import json
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from reelline.config import conf
from reelline.pipeline.script import write_script
from reelline.providers import CostLedger, get_llm

# Georgian for a Georgian channel, English for any other: the same story either way (a student competition).
FIX = Path(__file__).resolve().parent / "fixtures" / "script" / ("ka" if conf().channel.language == "ka" else "en")
DRAFT = (FIX / "draft.txt").read_text(encoding="utf-8").strip()
FINAL = (FIX / "final.txt").read_text(encoding="utf-8").strip()
_META = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
JOKE_1 = _META["joke_1"]
JOKE_2 = _META["joke_2"]
CAPTION = {**_META["caption"], "hashtags": [*conf().steps.caption.required_hashtags, *_META["caption"]["hashtags"]]}
CARD = _META["card"]


# ---- Fakes -----------------------------------------------------------------------------

_WRITER_MODEL = conf().models.writer.model


def response(text, *, thinking="SECRET THOUGHT", stop="end_turn", model=_WRITER_MODEL, tokens=(1000, 500, 300)):
    content = [
        SimpleNamespace(type="thinking", thinking=thinking, signature="sig"),
        SimpleNamespace(type="text", text=text),
    ]
    usage = SimpleNamespace(
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens_details=SimpleNamespace(thinking_tokens=tokens[2]),
    )
    return SimpleNamespace(content=content, usage=usage, stop_reason=stop, model=model, stop_details=None)


class FakeClaude:
    """client.messages.stream(**kw) (no create: ask() must stream): answers from
    `route(kw)` or a queue; records kw."""

    def __init__(self, answers=None, route=None):
        self.answers = list(answers or [])
        self.route = route
        self.requests: list[dict] = []
        self.messages = self

    @contextmanager
    def stream(self, **kw):
        yield SimpleNamespace(get_final_message=lambda: self.answer(kw))

    def answer(self, kw):
        self.requests.append(kw)
        if self.route is not None:
            return self.route(kw)
        prompt = kw["messages"][0]["content"] if kw.get("messages") else ""
        if isinstance(prompt, str) and is_prompt(prompt, PROOFREAD):  # a queue's proofread: no fixes
            return response(proofread_text(prompt))
        answer = self.answers.pop(0)
        return answer if isinstance(answer, SimpleNamespace) else response(answer)

    def prompts(self):
        return [r["messages"][0]["content"] for r in self.requests]


class FakeGemini:
    """Fact-checks: every draft sentence is a supported claim and every inserted one an
    exempt joke, except sentences containing a substring in rounds[i] (the i-th call's
    bad list): a distorted claim (for an inserted one, a news assertion)."""

    def __init__(self, rounds=None):
        self.rounds = list(rounds or [])
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        bad = self.rounds.pop(0) if self.rounds else []
        sentences = re.findall(r"^S(\d+)( \(inserted\)| \(card, [^)]*\)| \(fx, .*?“\))?: (.*)$", contents, re.M)
        claims, inserted = [], []
        for n, mark, text in sentences:
            wrong = any(b in text for b in bad)
            if mark == " (inserted)":
                inserted.append(
                    {
                        "sentence": int(n),
                        "news_assertion": wrong,
                        "reason": "asserts a number" if wrong else "a jab at the viewer",
                    }
                )
                if not wrong:
                    continue
            claims.append(
                {
                    "sentence": int(n),
                    "claim": f"claim {n}",
                    "verdict": "distorted" if wrong else "supported",
                    "evidence": None if wrong else "competition",
                    "note": "wrong number" if wrong else None,
                }
            )
        usage = SimpleNamespace(prompt_token_count=3000, candidates_token_count=400, thoughts_token_count=0)
        return SimpleNamespace(text=json.dumps({"claims": claims, "inserted": inserted}), usage_metadata=usage)


# What each pass's prompt says about itself, in the first channel's prompts and in the example's.
STORY = ("story writer", "You write the story pass")
PUNCHUP = ("head comedy writer", "You are the script editor")
PROOFREAD = ("Georgian proofreader", "You are the proofreader of")


def is_prompt(prompt: str, markers: tuple[str, ...]) -> bool:
    return any(m in prompt for m in markers)


def proofread_text(prompt: str) -> str:
    """The text a proofread prompt was filled with: what follows the words that precede {{TEXT}} in the channel's
    prompt, up to the words that follow it."""
    template = conf().steps.proofread.prompt.read_text(encoding="utf-8")
    before, _, after = template.partition("{{TEXT}}")
    text = prompt.rsplit(before[-20:], 1)[1]
    return text[: len(text) - len(after)] if after else text


def chain_route(punchups, stories=None, caption=None, proofread=None, cards=None):
    """A Claude router: story prompts get the draft, punch-up prompts the next punch-up
    answer, proofread prompts their text back (through `proofread`, a function of the
    text, when given), end-card prompts the next card JSON (CARD), caption prompts the
    caption JSON."""
    punchups, stories, cards = list(punchups), list(stories or [DRAFT]), list(cards or [CARD])

    def route(kw):
        prompt = kw["messages"][0]["content"]
        if is_prompt(prompt, STORY):
            return response(stories.pop(0) if len(stories) > 1 else stories[0])
        if is_prompt(prompt, PUNCHUP):
            return response(punchups.pop(0) if len(punchups) > 1 else punchups[0])
        if is_prompt(prompt, PROOFREAD):
            text = proofread_text(prompt)
            return response(proofread(text) if proofread else text)
        if "You write the end card" in prompt:
            card = cards.pop(0) if len(cards) > 1 else cards[0]
            return response(card if isinstance(card, str) else json.dumps(card, ensure_ascii=False))
        return response(json.dumps(caption or CAPTION))

    return route


def run_chain(w, claude, gemini):
    return write_script(
        w.conn, w.pick_id, claude=claude, gemini=gemini, source_names={"gtu_news": "GTU"}, log=lambda m: None
    )


# ---- The client ------------------------------------------------------------------------


def ask(client, messages, *, purpose, prompt_version, conn=None, script_id=None, schema=None):
    """The writer role's adapter (reelline/providers/anthropic.py) on a fake client, as story_pass() calls it."""
    return get_llm("writer", client).complete(
        messages,
        purpose=purpose,
        prompt_version=prompt_version,
        schema=schema,
        ledger=CostLedger(conn, script_id=script_id),
    )
