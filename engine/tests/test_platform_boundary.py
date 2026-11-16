"""Platform SDKs and API hosts stay in their adapters (reelline/publish, reelline/stats, reelline/storage):
the pipeline, the steps, the scripts and the channel tools go through the Publisher, StatsCollector and
BlobStore protocols. Checked on the code (the AST, docstrings aside): no import of boto3/botocore or a
Google API client, no YouTube, Graph, Buffer, TikTok or S3 host in a string. The hosts' defaults in the
schema (reelline/config/schema/: the settings that name them) are the one other place."""

import ast
from pathlib import Path

from reelline.paths import REPO_ROOT

ENGINE = REPO_ROOT / "engine"
SRC = ENGINE / "src" / "reelline"
ADAPTERS = (
    {SRC / "publish" / f"{n}.py" for n in ("buffer", "meta", "youtube", "tiktok")}
    | {SRC / "stats" / f"{n}.py" for n in ("buffer", "meta", "youtube", "tiktok")}
    | {SRC / "storage" / "s3.py"}
)
SETTINGS = set((SRC / "config" / "schema").glob("*.py"))
ROOTS = (ENGINE / "src", ENGINE / "scripts", REPO_ROOT / "channels")
SDKS = ("boto3", "botocore", "googleapiclient", "google_auth_oauthlib", "google.oauth2", "facebook", "tiktok_api")
HOSTS = (
    "googleapis.com",
    "graph.facebook.com",
    "rupload.facebook.com",
    "api.buffer.com",
    "tiktokapis.com",
    "business-api.tiktok.com",
    "amazonaws.com",
    "cloudflarestorage.com",
)


def hits(path: Path, root: Path = REPO_ROOT) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    out = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for name in names:
            if any(name == s or name.startswith(s + ".") for s in SDKS):
                out.append(f"{path.relative_to(root)}:{node.lineno} import {name}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            out += [f"{path.relative_to(root)}:{node.lineno} {h}" for h in HOSTS if h in node.value]
    return out


def test_platform_sdks_and_hosts_only_in_their_adapters():
    files = [
        p
        for root in ROOTS
        for p in root.rglob("*.py")
        if p not in ADAPTERS | SETTINGS and "node_modules" not in p.parts and ".venv" not in p.parts
    ]
    assert len(files) > 80
    assert [h for p in files for h in hits(p)] == []


def test_the_adapters_exist_and_name_a_host_or_sdk_themselves():
    assert all(p.is_file() for p in ADAPTERS)
    assert any(hits(p) for p in ADAPTERS)  # the check isn't blind to them


def test_the_check_sees_every_form(tmp_path):
    f = tmp_path / "x.py"
    f.write_text(
        '"""Docs may say graph.facebook.com."""\nimport boto3\nfrom botocore.config import Config\n'
        "def f():\n    '''amazonaws.com in a docstring is fine'''\n"
        "    return 'https://www.googleapis.com/youtube/v3' + 'https://open.tiktokapis.com' "
        "+ 'https://api.buffer.com'\n"
    )
    assert sorted(h.split(" ", 1)[1] for h in hits(f, tmp_path)) == [
        "api.buffer.com",
        "googleapis.com",
        "import boto3",
        "import botocore.config",
        "tiktokapis.com",
    ]
