"""The script's markup as motion signals: punchlines, bounce moments, emphasis, cut points."""

from __future__ import annotations

import re

from reelline.media import style as reel_style
from reelline.media.motion.base import config

# ---- The markup ---------------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"[.!?…][“”\"»)]*$")
_CLAUSE_END = re.compile(r"[,;:–—][“”\"»)]*$")
_EXCLAIM_END = re.compile(r"!+[“”\"»)]*$")


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def _paragraph_tokens(script) -> list[list[tuple[int, int, int]]]:
    """Per paragraph, its display tokens with spoken text: (token index, spoken letters before
    it starts, spoken letters by its end), counted from the paragraph's start (tags hold none)."""
    by_paragraph: dict[int, list[int]] = {}
    for i, token in enumerate(script.tokens):
        if token.pieces:
            by_paragraph.setdefault(token.paragraph, []).append(i)
    out, offset = [], 0
    for n, para in enumerate(script.paragraphs):
        rows = []
        for i in by_paragraph.get(n, []):
            pieces = script.tokens[i].pieces
            rows.append(
                (
                    i,
                    _letters(script.spoken[offset : min(p.spoken_start for p in pieces)]),
                    _letters(script.spoken[offset : max(p.spoken_end for p in pieces)]),
                )
            )
        out.append(rows)
        offset += len(para.spoken) + 2
    return out


def _tag_places(script, names: set[str]) -> list[tuple[int, str, int | None, int | None]]:
    """(paragraph, tag, the token before it, the token after it) for each tag in `names`, in
    script order (the tag line has none)."""
    from reelline.media.script_markup import TAG

    rows = _paragraph_tokens(script)
    out = []
    for n, para in enumerate(script.paragraphs):
        if para.tag:
            continue
        for m in TAG.finditer(para.tts):
            if m.group(1) not in names:
                continue
            before = _letters(TAG.sub("", para.tts[: m.start()]))
            prev = next((i for i, _, end in reversed(rows[n]) if 0 < end <= before), None)
            after = next((i for i, start, _ in rows[n] if start >= before), None)
            out.append((n, m.group(1), prev, after))
    return out


def punchline_tokens(script) -> list[tuple[int, str]]:
    """(token index, source) per punchline, in script order: the last display token before a
    laugh tag in its paragraph (source the tag), and the closing jab's last token ("jab")."""
    cfg = config()["punchline"]
    paragraphs = script.paragraphs
    jab = len(paragraphs) - 2 if cfg["closingJab"] and len(paragraphs) >= 2 and paragraphs[-1].tag else None
    rows = _paragraph_tokens(script)
    found: dict[int, str] = {}
    places = _tag_places(script, set(cfg["tags"]))
    for n, para in enumerate(paragraphs):
        if para.tag or para.flirt or para.details or not rows[n]:
            continue
        for _, tag, prev, _ in (p for p in places if p[0] == n):
            if prev is not None:
                found.setdefault(prev, tag)
        if n == jab:
            found[rows[n][-1][0]] = "jab"
    return sorted(found.items())


def joke_paragraphs(script) -> set[int]:
    """0-based paragraphs with a punchline in them, and the share line's (a joke; not in a style
    without jokes, reelline/media/style.py straight)."""
    share = {n for n, p in enumerate(script.paragraphs) if p.share} if reel_style.current().jokes else set()
    return {script.tokens[i].paragraph for i, _ in punchline_tokens(script)} | share


def punchlines(script, words: list[dict]) -> list[dict]:
    """[{paragraph (1-based), token, source, land}] in time order; land is the last word's end."""
    out = []
    for i, source in punchline_tokens(script):
        end = words[i]["end"]
        if end is not None:
            out.append(
                {
                    "paragraph": script.tokens[i].paragraph + 1,
                    "token": i,
                    "source": source,
                    "text": words[i]["text"],
                    "land": round(end, 3),
                }
            )
    return sorted(out, key=lambda p: p["land"])


def bounce_moments(script, words: list[dict]) -> list[dict]:
    """[{at, paragraph (1-based), why}] in time order, before spacing: each bounce.tags tag's
    place (the word before it ends; a tag before a paragraph's first word: leadS before that
    word starts), the closing jab's last word's end, the share line's last word's end."""
    cfg = config()["bounce"]
    out: list[dict] = []
    for n, tag, prev, after in _tag_places(script, set(cfg["tags"])):
        if prev is not None and words[prev]["end"] is not None:
            out.append({"at": round(words[prev]["end"], 3), "paragraph": n + 1, "why": tag})
        elif prev is None and after is not None and words[after]["start"] is not None:
            out.append(
                {"at": round(max(0.0, words[after]["start"] - float(cfg["leadS"])), 3), "paragraph": n + 1, "why": tag}
            )
    ends = {script.tokens[i].paragraph: i for i, t in enumerate(script.tokens) if t.pieces}
    if cfg["jab"]:
        out += [
            {"at": round(words[i]["end"], 3), "paragraph": script.tokens[i].paragraph + 1, "why": "jab"}
            for i, source in punchline_tokens(script)
            if source == "jab" and words[i]["end"] is not None
        ]
    if cfg["share"]:
        for n, para in enumerate(script.paragraphs):
            i = ends.get(n)
            if (
                para.share
                and i is not None
                and words[i]["end"] is not None
                and not any(m["paragraph"] == n + 1 and m["why"] != "share" for m in out)
            ):
                out.append({"at": round(words[i]["end"], 3), "paragraph": n + 1, "why": "share"})
    return sorted(out, key=lambda m: m["at"])


def emphasis_words(script, words: list[dict]) -> list[dict]:
    """[{token, at, end, paragraph (1-based), why}] for the lean-ins, before the caps: the
    first word after each <short pause> ("reveal"), a word with a digit in it ("number"; a
    glued group's first), a sentence's last word before '!' ("exclaim"); in time order."""
    cfg = config()["lean"]
    found: dict[int, str] = {}
    if cfg["afterPause"]:
        for _, _, _, after in _tag_places(script, {"short pause"}):
            if after is not None:
                found[after] = "reveal"
    for i, token in enumerate(script.tokens):
        if script.paragraphs[token.paragraph].tag or not token.pieces:
            continue
        prev = script.tokens[i - 1] if i else None
        if (
            cfg["numbers"]
            and any(ch.isdigit() for ch in token.text)
            and not (prev and prev.glue and any(ch.isdigit() for ch in prev.text))
        ):
            found.setdefault(i, "number")
        if cfg["exclaim"] and _EXCLAIM_END.search(token.text):
            found.setdefault(i, "exclaim")
    out = [
        {
            "token": i,
            "at": round(words[i]["start"], 3),
            "end": round(words[i]["end"], 3),
            "paragraph": script.tokens[i].paragraph + 1,
            "why": why,
        }
        for i, why in found.items()
        if words[i]["start"] is not None and words[i]["end"] is not None
    ]
    return sorted(out, key=lambda e: e["at"])


def stalk_pose(paragraph, mood: str | None) -> str | None:
    """The eye-stalk reaction for a paragraph: the first stalks.byStyle word in its @style,
    none for a style without one; no style: the reel mood's (stalks.byMood)."""
    cfg = config()["stalks"]
    style = " ".join((paragraph.style or "").lower().split())
    if style:
        return next((pose for word, pose in cfg["byStyle"] if word in style), None)
    return cfg["byMood"].get(mood) if mood else None


def cut_points(script, words: list[dict], body_end: float) -> list[dict]:
    """[{at, kind}] where the camera may cut: after a word that ends a sentence (.!?…, or its
    paragraph), a clause (,;:–—, or a lone dash after it) or a spoken phrase (the voice pauses
    framing.pauseS or longer before the next word), framing.cutLeadS before the next word
    starts (never inside a word, nor inside a group); kind "sentence", "clause" or "pause"."""
    lead, pause = float(config()["framing"]["cutLeadS"]), float(config()["framing"]["pauseS"])
    timed = [i for i, w in enumerate(words) if w["start"] is not None and w["end"] is not None]
    out = []
    for i, j in zip(timed, timed[1:], strict=False):
        token, w = script.tokens[i], words[i]
        if token.glue:
            continue
        between = [t.text for t in script.tokens[i + 1 : j]]
        if script.tokens[j].paragraph != token.paragraph or _SENTENCE_END.search(w["text"]):
            kind = "sentence"
        elif _CLAUSE_END.search(w["text"]) or any(not any(ch.isalnum() for ch in t) for t in between):
            kind = "clause"
        elif float(words[j]["start"]) - float(w["end"]) >= pause - 1e-6:
            kind = "pause"
        else:
            continue
        at = round(max(float(w["end"]), float(words[j]["start"]) - lead), 3)
        if at < body_end:
            out.append({"at": at, "kind": kind})
    return out
