"""A failed Remotion run says which composition failed and how, with the end of its
stderr (scripts/render_reel.py remotion()), and that reaches the reel's error, the job's
error and its Telegram report (job #66: "remotion still exited with 1" and nothing else)."""

import sys
from pathlib import Path

import pytest
from reelline.pipeline.produce import attempts_error

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import reelline.render.remotion as rr

COVER_ERROR = (
    ' Error  Cover headline "ბუდაპეშტის პროფესორი ევროპის უნივერსიტეტში" doesn\'t fit 960x660px in 3 lines at 96px'
)


def run_cover(tmp_path):
    with pytest.raises(rr.RenderError) as err:
        rr.remotion(["still", "Cover", str(tmp_path / "cover.png")], tmp_path / "p.json", tmp_path)
    return str(err.value)


def test_failure_names_the_composition_with_the_end_of_stderr(fake_npx, tmp_path, capfd, monkeypatch):
    # Progress on stdout, the error on stderr, as Remotion does; 25 stderr lines, one colored.
    monkeypatch.setenv("COVER_ERROR", COVER_ERROR)
    fake_npx(
        "echo 'Rendered 0/1'\n"
        'for i in $(seq 1 24); do echo "line $i" >&2; echo >&2; done\n'
        "printf '\\033[41m%s\\033[0m\\n' \"$COVER_ERROR\" >&2\n"
        "exit 1"
    )
    message = run_cover(tmp_path)
    lines = message.split("\n")
    assert lines[0] == "Remotion Cover still exited with 1; stderr:"
    assert lines[1:] == [f"line {i}" for i in range(6, 25)] + [COVER_ERROR]  # the last 20, blank lines skipped
    assert "Rendered" not in message
    out, err = capfd.readouterr()
    assert "Rendered 0/1" in out and "line 1\n" in err and COVER_ERROR in err  # everything still reaches the log


def test_a_killed_render_says_by_what(fake_npx, tmp_path):
    fake_npx("kill -9 $$")
    with pytest.raises(rr.RenderError) as err:
        rr.remotion(["render", "Reel", str(tmp_path / "reel.mp4")], tmp_path / "p.json", tmp_path)
    assert str(err.value) == "Remotion Reel render was killed by SIGKILL (nothing on stderr)"


def test_long_stderr_keeps_its_end(fake_npx, tmp_path):
    fake_npx('for i in $(seq 10 29); do printf "$i%.0s" $(seq 1 150) >&2; echo >&2; done; exit 1')
    message = run_cover(tmp_path)
    head, text = message.split("\n", 1)
    assert head == "Remotion Cover still exited with 1; stderr:"
    assert text.startswith("[...]") and len(text) == len("[...]") + rr.REMOTION_ERROR_CHARS
    last = text.split("\n")[-1]
    assert last == "29" * 100 + " [...]"  # each line cut to REMOTION_LINE_CHARS


def test_success_raises_nothing(fake_npx, tmp_path):
    fake_npx("echo warning >&2; exit 0")
    rr.remotion(["still", "Cover", str(tmp_path / "cover.png")], tmp_path / "p.json", tmp_path)


def test_remotion_gets_its_own_temp_dir_on_disk(fake_npx, tmp_path, monkeypatch):
    # Not the server's tmpfs /tmp: Remotion may write every frame there (rr.REMOTION_TMP).
    monkeypatch.setattr(rr, "REMOTION_TMP", tmp_path / "remotion_tmp")
    fake_npx(f'echo "$TMPDIR" > {tmp_path / "seen"}; touch "$TMPDIR/frame-0001.png"; exit 0')
    rr.remotion(["render", "Reel", str(tmp_path / "reel.mp4")], tmp_path / "p.json", tmp_path)
    seen = Path((tmp_path / "seen").read_text().strip())
    assert seen.parent == tmp_path / "remotion_tmp" and not seen.exists()  # removed after the run
    fake_npx("exit 1")
    run_cover(tmp_path)
    assert list((tmp_path / "remotion_tmp").iterdir()) == []  # after a failure too


def test_a_repeated_attempt_error_shows_once():
    first = f"Remotion Cover still exited with 1; stderr:\n{COVER_ERROR}"
    assert attempts_error([first, first]) == f"attempt 1: {first} | attempt 2: same as attempt 1"
    assert attempts_error(["a", "b", "b"]) == "attempt 1: a | attempt 2: b | attempt 3: same as attempt 2"
