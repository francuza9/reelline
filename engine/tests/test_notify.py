"""The Notifier adapters (reelline/notify/): telegram (rendering, the file kinds, the upload timeouts),
slack (an incoming webhook on a fake HTTP transport), log, the fake, the registry, chunking."""

import json

import httpx
import pytest
from reelline.config import conf
from reelline.notify import Notifier, NotifyError, buttons, get_notifier, send_chunks, split_text
from reelline.notify.fakes import FakeNotifier
from reelline.notify.log import LogNotifier
from reelline.notify.slack import SlackNotifier
from reelline.notify.telegram import TelegramNotifier
from tests.telegram_fakes import CallRecorder, recording_notifier

RETRY = [[("🔁 Retry", "j:5:rt")]]


def test_telegram_notifier_sends_files_by_kind(tmp_path):
    n = recording_notifier()
    timeouts = []
    record = n.bot.call
    n.bot.call = lambda m, p, files=None, timeout=None: (timeouts.append(timeout), record(m, p, files, timeout))[1]
    n.send_file("costs.csv", b"a,b\n", caption="rows")
    mp3 = tmp_path / "take.mp3"
    mp3.write_bytes(b"ID3")
    n.send_file("take.mp3", mp3, kind="audio", caption="Take 1", mime="audio/mpeg", meta={"title": "T1"})
    n.send_file("cover.png", b"png", kind="photo", caption="cover", mime="image/png")
    assert [(m, p, f) for m, p, f in n.bot.calls] == [
        ("sendDocument", {"caption": "rows"}, {"document": "costs.csv"}),
        ("sendAudio", {"title": "T1", "caption": "Take 1"}, {"audio": "take.mp3"}),
        ("sendPhoto", {"caption": "cover"}, {"photo": "cover.png"}),
    ]
    t = conf().review.telegram
    assert timeouts[0] is None and timeouts[1].write == t.upload_write_timeout_s == 600.0
    assert timeouts[1].read == t.upload_read_timeout_s == 180.0
    assert n.send("x", RETRY) == 4 and n.bot.calls[-1][1]["reply_markup"] == {
        "inline_keyboard": [[{"text": "🔁 Retry", "callback_data": "j:5:rt"}]]
    }


def test_telegram_errors_are_notify_errors_and_never_hold_the_token():
    class Down:
        def post(self, url, **kw):
            raise httpx.ConnectError(f"failed to reach {url}")

    from pathlib import Path

    from reelline.config.credentials import TelegramSettings
    from reelline.notify.telegram import TelegramBot

    bot = TelegramBot(TelegramSettings(bot_token="t0ken", chat_id="1", env_file=Path(".env")), http=Down())
    with pytest.raises(NotifyError) as info:
        TelegramNotifier(bot).send("hi")
    assert "t0ken" not in str(info.value) and "<token>" in str(info.value)


def slack(handler):
    return SlackNotifier(
        url="https://hooks.slack.test/T/B/secret", http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_slack_posts_text_with_its_actions_written_out():
    posts = []
    n = slack(lambda r: (posts.append(json.loads(r.content)), httpx.Response(200, text="ok"))[1])
    assert isinstance(n, Notifier)
    assert n.send("❌ Job #5 failed", RETRY) is None
    n.edit(1, "retrying")  # no edits: a new message
    n.send_file("costs.csv", b"a", caption="rows")
    assert posts == [
        {"text": "❌ Job #5 failed\n[🔁 Retry] j:5:rt"},
        {"text": "retrying"},
        {"text": "📎 document costs.csv\nrows"},
    ]
    with pytest.raises(NotifyError, match="HTTP 404") as info:
        slack(lambda r: httpx.Response(404, text="no_service")).send("x")
    assert "secret" not in str(info.value)

    def down(request):
        raise httpx.ConnectError(f"failed to reach {request.url}")

    with pytest.raises(NotifyError) as info:
        slack(down).send("x")
    assert "secret" not in str(info.value)


def test_slack_reads_its_url_from_the_env_file(monkeypatch):
    from reelline.config import credentials

    monkeypatch.setattr(credentials, "_read_env", lambda: ({"SLACK_WEBHOOK_URL": "https://h.test/x"}, None))
    assert SlackNotifier(http=object()).url == "https://h.test/x"
    monkeypatch.setattr(credentials, "_read_env", lambda: ({}, None))
    with pytest.raises(credentials.ConfigError, match="SLACK_WEBHOOK_URL"):
        SlackNotifier(http=object())


def test_log_notifier_writes_lines_and_files(tmp_path):
    lines = []
    n = LogNotifier(write=lines.append)
    assert n.send("⚠️ ingest failed", RETRY) == 1 and n.send("reply", reply_to=1) == 2
    n.edit(1, "fixed")
    assert lines == [
        "[notify #1] ⚠️ ingest failed\n[🔁 Retry] j:5:rt",
        "[notify #2 re #1] reply",
        "[notify #1 edited] fixed",
    ]
    path = tmp_path / "notify.log"
    LogNotifier(path=path).send_file("x.csv", b"abc", caption="rows")
    assert path.read_text().strip().endswith("[notify #1] document x.csv (3 bytes): rows")


def test_registry_and_chunks(monkeypatch):
    fake = FakeNotifier(text_limit=10)
    assert get_notifier(fake) is fake
    monkeypatch.setattr(conf().notify, "channel", "telegram")
    assert isinstance(get_notifier(CallRecorder()), TelegramNotifier)
    assert isinstance(get_notifier(lambda line: None, kind="log"), LogNotifier)
    monkeypatch.setattr(conf().notify, "channel", "log")
    assert isinstance(get_notifier(), LogNotifier)
    with pytest.raises(ValueError, match="no notifier"):
        get_notifier(kind="pigeon")
    send_chunks(fake, "aaaa\nbbbb\ncccc", RETRY)
    assert fake.texts() == ["aaaa\nbbbb", "cccc"] and [m.buttons for m in fake.sent] == [None, RETRY]
    assert split_text("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]
    assert buttons([[("a", "b")]]) == [[("a", "b")]]
    with pytest.raises(NotifyError):
        FakeNotifier(fail=True).send("x")
