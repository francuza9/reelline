"""Shared pytest fixtures and their helpers (registered by conftest.py)."""

import json
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from reelline.config import conf
from reelline.config.credentials import TelegramSettings
from reelline.notify.telegram import TelegramBot
from reelline.pipeline import db
from reelline.pipeline.pick import Picker
from reelline.pipeline.scoring import slot_datetime


@pytest.fixture
def two_slots(monkeypatch):
    """The slot machinery takes any channel.slots; these tests run it with the two slots of
    before 2026-10-16 (13:00 and 20:00), to cover one slot against another. The real
    schedule (one slot, 20:00) has its own tests: tests/test_run_daily.py ONE_SLOT."""
    monkeypatch.setattr(conf().channel, "slots", ("13:00", "20:00"))


DAY = date(2026, 10, 10)


SLOT_DT = slot_datetime(DAY, "13:00")


NOW = SLOT_DT - timedelta(hours=4)


TOKEN = "123456:SECRET-token"


CHAT = "42"


class FakeGemini:
    """client.models.generate_content(): assessments from self.assess (story id -> fields),
    briefs from self.brief."""

    def __init__(self):
        self.assess: dict[int, dict] = {}
        self.brief: dict = {}
        self.calls: list[str] = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        if "screen news stories" in contents:
            ids = [int(i) for i in re.findall(r'<story id="(\d+)">', contents)]
            data = {"stories": [{"id": i, **self.assess[i]} for i in ids]}
        else:
            data = self.brief
        usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=100, thoughts_token_count=20)
        return SimpleNamespace(text=json.dumps(data), usage_metadata=usage)


class FakeResponse:
    def __init__(self, result):
        self.status_code = 200
        self._body = {"ok": True, "result": result}

    def json(self):
        return self._body


class FakeHttp:
    """Bot API over a fake HTTP client: records calls, serves scripted getUpdates batches."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.updates: list[list[dict]] = []
        self.next_message_id = 100
        self.empty_polls = 0

    def post(self, url, json):
        method = url.rsplit("/", 1)[1]
        assert TOKEN in url
        self.calls.append((method, json))
        if method == "sendMessage":
            self.next_message_id += 1
            return FakeResponse({"message_id": self.next_message_id})
        if method == "getUpdates":
            if self.updates:
                return FakeResponse(self.updates.pop(0))
            self.empty_polls += 1
            assert self.empty_polls < 5, "listen kept polling: the menu never resolved"
            return FakeResponse([])
        return FakeResponse(True)

    def methods(self):
        return [m for m, _ in self.calls]


def bot_with(http):
    return TelegramBot(TelegramSettings(bot_token=TOKEN, chat_id=CHAT, env_file=Path("x")), http=http)


@pytest.fixture
def pick_world(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    gemini = FakeGemini()
    gemini.brief = {
        "what_it_is": "A thing.",
        "what_participants_do": "",
        "who_is_behind": "TSU",
        "purpose": "",
        "who_can_apply_age": "18-29",
        "who_can_apply_status": None,
        "requirements": None,
        "cost": "free",
        "deadline": None,
        "how_to_apply": "not stated.",
        "where_to_apply": "https://x.ge/apply",
        "when": None,
        "key_numbers": ["30 places"],
        "caveats": [],
    }
    http = FakeHttp()
    clock = {"now": NOW}
    picker = Picker(
        conn,
        source_names={"src": "Source Name"},
        client_factory=lambda: gemini,
        channel_factory=lambda: bot_with(http),
        now=lambda: clock["now"],
        log=lambda m: None,
    )
    picker.briefs_dir = tmp_path / "briefs"
    return SimpleNamespace(conn=conn, gemini=gemini, http=http, clock=clock, picker=picker, tmp=tmp_path)
