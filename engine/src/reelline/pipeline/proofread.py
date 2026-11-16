"""The proofread pass (channel docs/scripts.md): after punch-up and before the fact-check, the
final script goes once more to the proofreader role (models.proofreader) with prompts/proofread.md,
which may fix only Georgian grammar, spelling, punctuation and unnatural phrasing.
The caption hook gets the same pass (reelline/pipeline/caption.py).

A call that fails (an LLMError, or a connection that dropped on every try:
reelline/providers/retry.py) keeps the original, with a warning. The answer is used only when every
guard passes; otherwise the original stays and a warning is logged (the reason is stored):
  script  the parser passes (the channel's script_markup.parse); the same paragraphs, the same
          directives in each (names in order; @style, @image and @fx values unchanged), the same
          vocal tags in order, the same number of {display|spoken} groups in order, each
          group's two sides consistent (group_problem()); the display word count within
          steps.proofread.word_tolerance of the original's
  hook    the same number of sentences, no markup, the caption checks pass
          (reelline/pipeline/caption.py caption_problems()), the word count within the tolerance

Every run is stored in the proofreads table (migration 23): before, after, applied or
not and why, and the word-level fixes (fixes(): each changed run of words with a word of
context either side), for tuning the prompts later. The review's status block shows
"✍️ grammar: N fixes" and the fixes (reelline/pipeline/produce.py).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from reelline import language
from reelline.config import conf
from reelline.media.prompts import fill
from reelline.media.script_markup import GROUP, TAG, MarkupError, parse
from reelline.pipeline import db
from reelline.providers import CostLedger, LLMError, LLMReply, TransportFailure, get_llm

_BLANK = re.compile(r"\n[ \t]*\n")
_DIRECTIVE = re.compile(r"^\s*@(\w+)(?::(.*))?$")  # "@flirt" has no value
_GEORGIAN_TAIL = language.current().script_tail
KEEP_VALUES = ("style", "image", "fx")  # directive values the proofreader never touches


@dataclass
class Proofread:
    kind: str  # script | hook
    original: str
    text: str  # what's used: the proofread text, or the original
    applied: bool = False
    proofread: str | None = None  # the model's answer as it came
    reason: str | None = None  # why the original stayed
    fixes: list[dict] = field(default_factory=list)
    replies: list[LLMReply] = field(default_factory=list)


def strip_fence(text: str) -> str:
    m = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", text.strip(), re.DOTALL)
    return m.group(1).strip() if m else text.strip()


# ---- Guards ---------------------------------------------------------------------------------


def blocks(source: str) -> list[str]:
    return [b for b in _BLANK.split(source.strip()) if b.strip()]


def shape(block: str) -> dict:
    """A paragraph's structure: its directives (names in order, the English values), its
    vocal tags in order, its groups."""
    names, kept = [], {}
    for line in block.splitlines():
        m = _DIRECTIVE.match(line)
        if m:
            names.append(m.group(1))
            if m.group(1) in KEEP_VALUES:
                value = " ".join((m.group(2) or "").split())
                if m.group(1) == "image":
                    # A second or third picture's `| word` is Georgian: only its query is kept.
                    kept.setdefault("image", []).append(value.split("|")[0].strip())
                    continue
                kept[m.group(1)] = value
    return {"directives": names, "values": kept, "tags": TAG.findall(block), "groups": GROUP.findall(block)}


def split_display(display: str) -> tuple[str, str]:
    """(the Latin / digit core, its Georgian ending): "Meta-<ending>" -> ("Meta-", "<ending>")."""
    m = _GEORGIAN_TAIL.search(display)
    return (display[: m.start()], m.group()) if m else (display, "")


def group_problem(old: tuple[str, str], new: tuple[str, str]) -> str | None:
    """A group may change only its Georgian ending, on both sides at once; None if fine."""
    if old == new:
        return None
    (od, osp), (nd, nsp) = old, new
    ocore, oend = split_display(od)
    ncore, nend = split_display(nd)
    if ocore != ncore:
        return f"group {{{od}|{osp}}} became {{{nd}|{nsp}}}: its Latin or digits changed"
    if oend == nend:
        return f"group {{{od}|{osp}}} became {{{nd}|{nsp}}}: the spoken side changed alone"
    if not nend or not nsp.endswith(nend) or nsp == osp:
        return f"group {{{od}|{osp}}} became {{{nd}|{nsp}}}: its two sides don't match"
    return None


def word_problem(before: str, after: str) -> str | None:
    a, b = len(before.split()), len(after.split())
    limit = conf().steps.proofread.word_tolerance
    return None if a and abs(b - a) <= limit * a else f"{a} words became {b} (more than {limit:.0%} off)"


def script_problems(original: str, proofread: str) -> list[str]:
    """Why the proofread script can't replace the original ([] if it can)."""
    try:
        new = parse(proofread)
    except MarkupError as exc:
        return [f"the parser rejected it: {exc}"]
    old_blocks, new_blocks = blocks(original), blocks(proofread)
    if len(old_blocks) != len(new_blocks):
        return [f"{len(old_blocks)} paragraphs became {len(new_blocks)}"]
    problems = []
    cover = old_blocks[0].lstrip().startswith("@cover:")
    for n, (ob, nb) in enumerate(zip(old_blocks, new_blocks, strict=False), 0 if cover else 1):
        o, p = shape(ob), shape(nb)
        n = n or "of the cover"  # numbered as the parser does: the cover isn't one
        if o["directives"] != p["directives"]:
            problems.append(f"paragraph {n}: directives {o['directives']} became {p['directives']}")
        if o["values"] != p["values"]:
            problems.append(f"paragraph {n}: @style / @image / @fx changed")
        if o["tags"] != p["tags"]:
            problems.append(f"paragraph {n}: vocal tags {o['tags']} became {p['tags']}")
        if len(o["groups"]) != len(p["groups"]):
            problems.append(f"paragraph {n}: {len(o['groups'])} groups became {len(p['groups'])}")
            continue
        problems += [
            f"paragraph {n}: {why}"
            for og, ng in zip(o["groups"], p["groups"], strict=False)
            if (why := group_problem(og, ng))
        ]
    why = word_problem(parse(original).display, new.display)
    return problems + ([why] if why else [])


def hook_problems(original: str, proofread: str, title: str, hashtags: list[str]) -> list[str]:
    from reelline.pipeline.caption import caption_problems
    from reelline.pipeline.script import sentences

    problems = []
    if len(sentences(original)) != len(sentences(proofread)):
        problems.append(f"{len(sentences(original))} sentences became {len(sentences(proofread))}")
    if any(ch in proofread for ch in "{}|<>@"):
        problems.append("markup in the hook")
    problems += caption_problems({"hook": proofread, "youtube_title": title, "hashtags": hashtags})
    why = word_problem(original, proofread)
    return problems + ([why] if why else [])


# ---- Fixes --------------------------------------------------------------------------------


def plain(text: str, kind: str) -> str:
    """The words a viewer sees: a script's display text (the tag left out), or the hook."""
    if kind != "script":
        return " ".join(text.split())
    return " ".join(p.display for p in parse(text).paragraphs if not p.tag)


def fixes(before: str, after: str) -> list[dict]:
    """Each changed run of words, with a word of context either side: [{"before", "after"}]."""
    a, b = before.split(), after.split()
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        lo_a, hi_a, lo_b, hi_b = max(0, i1 - 1), min(len(a), i2 + 1), max(0, j1 - 1), min(len(b), j2 + 1)
        out.append({"before": " ".join(a[lo_a:hi_a]), "after": " ".join(b[lo_b:hi_b])})
    return out


# ---- The pass -----------------------------------------------------------------------------


def _ask(client, kind: str, text: str, conn, script_id) -> LLMReply:
    prompt = fill(
        conf().steps.proofread.prompt.read_text(encoding="utf-8"),
        {"KIND": conf().steps.proofread.kinds[kind], "TEXT": text},
        conf().steps.proofread.prompt.name,
    )
    purpose = "proofread" if kind == "script" else "proofread_hook"
    return get_llm("proofreader", client).complete(
        [{"role": "user", "content": prompt}],
        purpose=purpose,
        prompt_version=conf().steps.proofread.prompt_version,
        ledger=CostLedger(conn, script_id=script_id),
    )


def run(
    client,
    kind: str,
    text: str,
    problems_of,
    *,
    conn: sqlite3.Connection | None = None,
    script_id: int | None = None,
    log=lambda m: print(m, flush=True),
) -> Proofread:
    """Proofread `text` (kind script | hook); problems_of(original, proofread) -> [why not]."""
    result = Proofread(kind, text, text)
    if not conf().steps.proofread.enabled:
        result.reason = "proofread is off (steps.proofread.enabled)"
        return result
    try:
        reply = _ask(client, kind, text, conn, script_id)
    except (LLMError, TransportFailure) as exc:
        result.reason = f"the call failed: {exc}"
        log(f"  warning: proofread ({kind}) kept the original: {result.reason}")
        return result
    result.replies.append(reply)
    result.proofread = strip_fence(reply.text)
    problems = problems_of(text, result.proofread)
    if problems:
        result.reason = "; ".join(problems)[:1000]
        log(f"  warning: proofread ({kind}) kept the original: {result.reason}")
        return result
    result.fixes = fixes(plain(text, kind), plain(result.proofread, kind))
    result.applied, result.text = True, result.proofread
    log(f"  proofread ({kind}): {len(result.fixes)} fix(es)")
    return result


def proofread_script(client, script_text: str, **kw) -> Proofread:
    return run(client, "script", script_text, script_problems, **kw)


def proofread_hook(client, hook: str, title: str, hashtags: list[str], **kw) -> Proofread:
    return run(client, "hook", hook, lambda a, b: hook_problems(a, b, title, hashtags), **kw)


def save(conn: sqlite3.Connection, script_id: int | None, result: Proofread) -> None:
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO proofreads (script_id, kind, original, proofread, applied, fixes, reason,
                            prompt_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                script_id,
                result.kind,
                result.original,
                result.proofread,
                int(result.applied),
                json.dumps(result.fixes, ensure_ascii=False),
                result.reason,
                conf().steps.proofread.prompt_version,
                db.now_iso(),
            ),
        )


def latest(conn: sqlite3.Connection, script_id: int, kind: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM proofreads WHERE script_id = ? AND kind = ? ORDER BY id DESC LIMIT 1", (script_id, kind)
    ).fetchone()


def status_lines(conn: sqlite3.Connection, script_id: int, max_chars: int = 90) -> list[str]:
    """The review's grammar lines: "✍️ grammar: N fixes" (script, hook) and each fix."""
    rows = [r for r in (latest(conn, script_id, "script"), latest(conn, script_id, "hook")) if r is not None]
    if not rows:
        return []
    fixed = [(r["kind"], f) for r in rows if r["applied"] for f in json.loads(r["fixes"])]
    kept = [f"{r['kind']} kept as it was: {r['reason']}" for r in rows if not r["applied"] and r["reason"]]
    head = f"✍️ grammar: {len(fixed)} fix{'' if len(fixed) == 1 else 'es'}"
    if fixed:
        by_kind = {k: sum(1 for kk, _ in fixed if kk == k) for k in ("script", "hook")}
        head += " (" + ", ".join(f"{k} {n}" for k, n in by_kind.items() if n) + ")"
    out = [head]
    out += [f"  {f['before'][:max_chars]} → {f['after'][:max_chars]}" for _, f in fixed]
    out += [f"  ⚠️ {k[:300]}" for k in kept]
    return out
