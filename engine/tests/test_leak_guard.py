"""The leak guard (reelline/dev/leak_guard.py): each rule, the allowlist and the repository itself."""

from pathlib import Path

import pytest
from reelline.dev import leak_guard as lg

NAME = "y" + "vito"


def tree(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    root = tmp_path / "engine"
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return root


def allow(tmp_path: Path, body: str = "") -> Path:
    p = tmp_path / "allow.toml"
    p.write_text(body, encoding="utf-8")
    return p


def rules_of(root: Path) -> set[str]:
    return {h.rule for h in lg.scan(root)}


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (f"channel = '{NAME}'", "name"),
        ("# " + NAME.upper() + " channel", "name"),
        ("handle = '\u10d8\u10e7\u10d5\u10d8\u10d7\u10dd'", "name"),
        (f"https://{NAME}.pages.dev/x", "pages-dev"),
        ("ssh het" + "zner-box", "server"),
        (f"cd /home/{NAME}/app", "server"),
        ("contact me at someone@gmail" + ".com", "email"),
        ("call +" + "995555123456", "phone"),
        ("call +" + "995 555 123 456", "phone"),
        ("TELEGRAM_CHAT_ID=-100" + "1234567890", "chat-id"),
        ("chat_id: " + "123456789", "chat-id"),
        ("http://192.168" + ".1.20:8080/x", "private-url"),
        ("https://abc.r2.cloudflare" + "storage.com/b", "private-url"),
        ("https://my-box.ts" + ".net/", "private-url"),
    ],
)
def test_each_rule_finds_its_leak(tmp_path, text, rule):
    root = tree(tmp_path, {"src/a.py": text + "\n"})
    assert rule in rules_of(root)


@pytest.mark.parametrize(
    "text",
    [
        "mail someone@example.com",
        "Co-Authored-By: x <noreply@anthropic.com>",
        "12345 678",
        "http://127.0.0.1:8080/callback",
        "http://localhost:3000",
        "unit foo@bar.service",
        "price +5 or +10",
    ],
)
def test_ordinary_text_is_not_a_leak(tmp_path, text):
    assert rules_of(tree(tmp_path, {"src/a.py": text + "\n"})) == set()


def test_a_big_file_is_a_leak_and_a_binary_file_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "MAX_BYTES", 100)
    root = tree(tmp_path, {"big.dat": "x" * 200, "ok.bin": b"\xff\xfe" + NAME.encode()})
    hits = lg.scan(root)
    assert [(h.path, h.rule) for h in hits] == [("big.dat", "big-file")]


def test_node_modules_and_caches_are_skipped_without_git(tmp_path):
    root = tree(tmp_path, {"remotion/node_modules/x.js": NAME, "__pycache__/a.py": NAME, "a.py": "ok"})
    assert lg.scan(root) == []


def test_hits_carry_the_file_and_line(tmp_path):
    root = tree(tmp_path, {"src/a.py": f"ok\nx = '{NAME}'\n"})
    (hit,) = lg.scan(root)
    assert (hit.path, hit.line, hit.rule) == ("src/a.py", 2, "name")
    assert str(hit).startswith("src/a.py:2: name: ")


def test_an_unlisted_leak_fails_and_a_listed_one_passes(tmp_path):
    root = tree(tmp_path, {"src/a.py": f"x = '{NAME}'\n"})
    report = lg.check(root, allow(tmp_path))
    assert not report.ok and len(report.leaks) == 1
    entry = '[[allow]]\npath = "src/*.py"\nrules = ["name"]\nreason = "r"\nphase = "5b"\nmax_hits = 1\n'
    report = lg.check(root, allow(tmp_path, entry))
    assert report.ok and report.allowed == {"src/*.py": 1}


def test_an_entry_covers_only_its_rules_and_paths(tmp_path):
    root = tree(tmp_path, {"src/a.py": f"x = '{NAME}'  # a@b" + ".com\n", "tests/t.py": f"'{NAME}'\n"})
    entry = '[[allow]]\npath = "src/*"\nrules = ["name"]\nreason = "r"\nphase = "5b"\n'
    report = lg.check(root, allow(tmp_path, entry))
    assert {(h.path, h.rule) for h in report.leaks} == {("src/a.py", "email"), ("tests/t.py", "name")}


def test_an_entry_that_excuses_nothing_fails(tmp_path):
    root = tree(tmp_path, {"src/a.py": "fine\n"})
    entry = '[[allow]]\npath = "src/*"\nrules = ["*"]\nreason = "r"\nphase = "5b"\n'
    report = lg.check(root, allow(tmp_path, entry))
    assert not report.ok and [e.path for e in report.unused] == ["src/*"]


def test_an_entry_over_its_max_hits_fails(tmp_path):
    root = tree(tmp_path, {"src/a.py": f"'{NAME}'\n'{NAME}'\n"})
    entry = '[[allow]]\npath = "src/*"\nrules = ["name"]\nreason = "r"\nphase = "5b"\nmax_hits = 1\n'
    report = lg.check(root, allow(tmp_path, entry))
    assert not report.ok and [e.path for e in report.over] == ["src/*"]


@pytest.mark.parametrize(
    ("entry", "complaint"),
    [
        ('path = "a"\nrules = ["name"]\nphase = "5b"', "lacks reason"),
        ('path = "a"\nrules = ["name"]\nreason = "r"', "lacks phase"),
        ('path = "a"\nrules = ["nope"]\nreason = "r"\nphase = "5b"', "unknown rules"),
    ],
)
def test_an_entry_needs_a_reason_and_a_phase(tmp_path, entry, complaint):
    with pytest.raises(ValueError, match=complaint):
        lg.load_allowlist(allow(tmp_path, f"[[allow]]\n{entry}\n"))


def test_main_prints_and_returns_the_verdict(tmp_path, capsys):
    root = tree(tmp_path, {"src/a.py": f"'{NAME}'\n"})
    assert lg.main(root, allow(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "LEAK src/a.py:1: name" in out and out.rstrip().endswith("leak guard: FAILED")


def test_the_repository_passes_with_its_allowlist(capsys):
    root = lg.ALLOWLIST.parent
    assert lg.main(root) == 0, capsys.readouterr().out


def test_every_allowlist_entry_has_a_reason_and_a_phase():
    entries = lg.load_allowlist(lg.ALLOWLIST)
    assert all(e.reason and e.phase and e.max_hits for e in entries)  # (the public tree's list is empty)
