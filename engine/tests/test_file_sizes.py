"""No engine source file is over 600 lines: split a module by responsibility (a package of
submodules, the old import path re-exporting its names) before it grows past that."""

from reelline.paths import REPO_ROOT

SRC = REPO_ROOT / "engine" / "src" / "reelline"
MAX_LINES = 600


def test_no_engine_source_file_is_over_600_lines():
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 100  # the walk found the package
    big = [(p, len(p.read_text(encoding="utf-8").splitlines())) for p in files]
    offenders = [f"{p.relative_to(REPO_ROOT)}: {n} lines" for p, n in big if n > MAX_LINES]
    assert not offenders, f"engine files over {MAX_LINES} lines:\n" + "\n".join(offenders)
