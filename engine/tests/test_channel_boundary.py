"""Telegram stays in its two adapters (reelline/notify/telegram.py, reelline/review/telegram.py): the
steps, the bot, the scripts and the channel tools go through the Notifier and ReviewChannel
protocols. Checked on the code (the AST, docstrings and comments aside): no import of a Telegram
adapter outside the adapters and their registries, no Telegram credentials loaded, no Bot API
method, markup field or API URL in a string. The one URL elsewhere is the setting's default
(review.telegram.api_base, reelline/config/schema/channel.py)."""

import ast
from pathlib import Path

from reelline.paths import REPO_ROOT

ENGINE = REPO_ROOT / "engine"
SRC = ENGINE / "src" / "reelline"
ADAPTERS = {SRC / "notify" / "telegram.py", SRC / "review" / "telegram.py"}
REGISTRIES = {SRC / "notify" / "registry.py", SRC / "review" / "registry.py"}
ROOTS = (ENGINE / "src", ENGINE / "scripts", REPO_ROOT / "channels")
TELEGRAM_MODULES = ("reelline.notify.telegram", "reelline.review.telegram", "telegram", "aiogram", "telebot")
TELEGRAM_NAMES = ("TelegramBot", "TelegramNotifier", "TelegramReview", "load_telegram")
BOT_API = (
    "api.telegram.org",
    "sendMessage",
    "editMessageText",
    "editMessageReplyMarkup",
    "answerCallbackQuery",
    "getUpdates",
    "sendVideo",
    "sendAudio",
    "sendPhoto",
    "sendDocument",
    "inline_keyboard",
    "callback_data",
    "callback_query",
    "reply_markup",
)
ALLOWED = {
    (SRC / "config" / "schema" / "channel.py", "api.telegram.org"),  # the api_base setting's default
    (SRC / "config" / "credentials.py", "load_telegram"),
}  # where the accessor is defined


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def telegram_hits(path: Path, root: Path = REPO_ROOT) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    docs, hits = _docstrings(tree), []

    def hit(node, what):
        if (path, what) not in ALLOWED:
            hits.append(f"{path.relative_to(root)}:{node.lineno} {what}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if any(a.name == m or a.name.startswith(m + ".") for m in TELEGRAM_MODULES):
                    hit(node, a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 0 and any(node.module == m or node.module.startswith(m + ".") for m in TELEGRAM_MODULES):
                hit(node, node.module)
            for a in node.names:
                if a.name in TELEGRAM_NAMES:
                    hit(node, a.name)
        elif isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in TELEGRAM_NAMES:
                hit(node, name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            for word in BOT_API:
                if word in node.value:
                    hit(node, word)
    return hits


def test_telegram_only_in_its_adapters():
    files = [
        p
        for root in ROOTS
        for p in root.rglob("*.py")
        if p not in ADAPTERS | REGISTRIES and "node_modules" not in p.parts and ".venv" not in p.parts
    ]
    assert len(files) > 80
    assert [h for p in files for h in telegram_hits(p)] == []


def test_the_registries_name_the_adapters_only_lazily():
    for path in REGISTRIES:
        top = [
            n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.Import | ast.ImportFrom)
        ]
        assert not [n for n in top if "telegram" in (getattr(n, "module", "") or "")], path


def test_the_check_sees_every_form(tmp_path):
    f = tmp_path / "x.py"
    f.write_text(
        '"""Docs may say sendMessage."""\n'
        "from reelline.notify.telegram import TelegramBot\nimport telegram\n"
        "def f(bot):\n    '''inline_keyboard in a docstring is fine'''\n"
        "    bot.call('sendMessage', {})\n    x = {'reply_markup': 1}\n"
        "    return load_telegram() or 'https://api.telegram.org/bot'\n"
    )
    hits = sorted(h.split(" ", 1)[1] for h in telegram_hits(f, tmp_path))
    assert hits == [
        "TelegramBot",
        "api.telegram.org",
        "load_telegram",
        "reelline.notify.telegram",
        "reply_markup",
        "sendMessage",
        "telegram",
    ]
