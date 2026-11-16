"""Only reelline/providers/ imports a model vendor's SDK: the steps, the media code, the scripts and the
channel tools go through the provider protocols (reelline/providers/base.py)."""

import ast
from pathlib import Path

from reelline.paths import REPO_ROOT

VENDOR_SDKS = (
    "anthropic",
    "google.genai",
    "google.generativeai",
    "openai",
    "httpx2",
    "elevenlabs",
    "cohere",
    "mistralai",
    "groq",
    "together",
    "replicate",
    "vertexai",
    "google.cloud.aiplatform",
    "google.cloud.texttospeech",
    "boto3.bedrock",
)
ENGINE = REPO_ROOT / "engine"
PROVIDERS = ENGINE / "src" / "reelline" / "providers"
ROOTS = (ENGINE / "src", ENGINE / "scripts", REPO_ROOT / "channels")


def vendor_imports(path: Path, root: Path = REPO_ROOT) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        else:
            continue
        for name in names:
            if any(name == sdk or name.startswith(sdk + ".") for sdk in VENDOR_SDKS):
                found.append(f"{path.relative_to(root)}:{node.lineno} {name}")
    return found


def test_no_vendor_sdk_outside_providers():
    files = [
        p
        for root in ROOTS
        for p in root.rglob("*.py")
        if PROVIDERS not in p.parents and "node_modules" not in p.parts and ".venv" not in p.parts
    ]
    assert len(files) > 80  # the walk saw the code
    assert [hit for p in files for hit in vendor_imports(p)] == []


def test_the_check_sees_every_import_form(tmp_path):
    f = tmp_path / "x.py"
    f.write_text(
        "import anthropic\nfrom google import genai\nfrom google.genai import types\n"
        "def f():\n    import openai.types\n    from httpx2 import Client\nimport httpx\nimport google.auth\n"
    )
    hits = sorted(h.split(" ", 1)[1] for h in vendor_imports(f, tmp_path))
    assert hits == [
        "anthropic",
        "google.genai",
        "google.genai",
        "google.genai.types",
        "httpx2",
        "httpx2.Client",
        "openai.types",
    ]


def test_the_providers_import_their_sdks_lazily():
    """Importing an adapter needs no SDK: only connecting or calling does (reelline[anthropic,google])."""
    for name in ("anthropic.py", "google.py", "openai.py", "registry.py", "base.py", "costs.py", "retry.py"):
        tree = ast.parse((PROVIDERS / name).read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
        assert not [h for n in top for h in _names(n) if h.split(".")[0] in ("anthropic", "google", "httpx2")], name


def _names(node) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    return [node.module or ""] if node.level == 0 else []
