"""Punch-up's checks: opening, explanation, details, share, fx and straight-style problems."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from reelline import language
from reelline.config import conf
from reelline.media import style as reel_style
from reelline.media.script_markup import (
    OPENING_EMOTIONS,
    Script,
)
from reelline.pipeline.script.base import _TAG, _THOUSANDS
from reelline.pipeline.script.facts import uncarded_facts
from reelline.pipeline.script.lock import display_text, draft_lock, normalize, sentences
from reelline.pipeline.script.text import _same, draft_paragraphs, explanation, spoken_words


def explanation_problems(script: Script, draft: str) -> list[str]:
    """The plain explanation is locked like the first sentence (prompts/punchup.md "The plain
    explanation"): unchanged, right after paragraph 1's last sentence (no joke between), and
    the flirt line not before it. [] when it holds, or when the draft has no explanation."""
    sentence = explanation(draft)
    if sentence is None:
        return []
    final = sentences(display_text(script))
    at = next((j for j, s in enumerate(final) if _same(sentence, s)), None)
    if at is None:
        return [
            f"the plain explanation (the first sentence of the draft's paragraph 2) must stay unchanged, never "
            f"shortened into a label: „{sentence}“"
        ]
    problems = []
    last = sentences(draft_paragraphs(draft)[0])[-1]
    if (
        at > 0
        and SequenceMatcher(None, normalize(last), normalize(final[at - 1])).ratio() < conf().steps.script.changed_ratio
    ):
        problems.append(
            f"nothing goes between paragraph 1 and the plain explanation „{sentence}“, but „{final[at - 1]}“ "
            f"does: put it after the explanation"
        )
    home = next(
        i
        for i, p in enumerate(script.paragraphs)
        if not p.tag and not p.flirt and any(_same(sentence, s) for s in sentences(p.display))
    )
    if any(p.flirt for p in script.paragraphs[:home]):
        problems.append(f"the flirt line goes before the plain explanation „{sentence}“: put it after it")
    return problems


def paragraph1_problems(script: Script, draft: str) -> list[str]:
    """Punch-up adds nothing to paragraph 1 (prompts/punchup.md "The opening"): the script's
    paragraph 1 is the draft's, sentence for sentence (draft_lock()'s same_ratio, punctuation
    ignored), not split, with no vocal tag, so it gets no longer and its stake stays first.
    [] when it holds, or for a draft of one paragraph."""
    paragraphs = draft_paragraphs(draft)
    if len(paragraphs) < 2:
        return []
    want, first = sentences(paragraphs[0]), script.paragraphs[0]
    have = sentences(first.display)
    problems = []
    if len(have) != len(want) or not all(_same(h, w) for h, w in zip(have, want, strict=False)):
        problems.append(
            f"paragraph 1 is the draft's paragraph 1 exactly, in one paragraph, with nothing added (no "
            f"joke, no sentence of your own) and nothing changed: „{paragraphs[0]}“; the script's "
            f"paragraph 1 is „{first.display}“"
        )
    tags = _TAG.findall(first.tts)
    if tags:
        problems.append(f"no vocal tag in paragraph 1 (it only makes it longer): take out {' '.join(tags)}")
    return problems


def opening_problems(script: Script, previous: str | None, draft: str | None = None) -> list[str]:
    """What's wrong with the script's opening (prompts/punchup.md "The opening" and "The plain
    explanation"), [] if nothing. A plain opener (no opening emotion) is allowed. With the
    draft: the script starts with its first sentence, unchanged (draft_lock()'s same_ratio),
    so no title goes before it; then paragraph 1 is the draft's as it is (paragraph1_problems())
    and the plain explanation holds (explanation_problems())."""
    problems = []
    first_draft = sentences(draft)[:1] if draft else []
    first_final = sentences(display_text(script))[:1]
    if first_draft and first_final and not _same(first_draft[0], first_final[0]):
        problems.append(
            f"the script must start with the draft's first sentence, unchanged, with nothing before "
            f"it (no title or headline): „{first_draft[0]}“; it starts with „{first_final[0]}“"
        )
    elif draft:
        problems += paragraph1_problems(script, draft)
    if draft:
        problems += explanation_problems(script, draft)
    if script.opening is not None and script.opening == previous:
        others = ", ".join(e for e in OPENING_EMOTIONS if e != previous)
        problems.append(f"the previous video opened with {previous}; paragraph 1 needs a different one: {others}")
    image = script.paragraphs[0].image
    if image is None or image.kind == "none":
        problems.append(
            "paragraph 1 needs an @image (the thing the first sentence is about, or the organization's "
            "or product's logo), never none"
        )
    return problems


def _home(script: Script, sentence: str, ratio: float) -> int | None:
    """Index of the (non-tag, non-flirt, non-details, non-share) paragraph holding a sentence that
    matches `sentence` at least `ratio` (normalized), the best match; None if none does."""
    best, best_ratio = None, ratio
    target = normalize(sentence)
    for i, p in enumerate(script.paragraphs):
        if p.tag or p.flirt or p.details or p.share:
            continue
        for s in sentences(p.display):
            r = 1.0 if normalize(s) == target else SequenceMatcher(None, target, normalize(s)).ratio()
            if r >= best_ratio:
                best, best_ratio = i, r
    return best


def details_problems(script: Script, draft: str | None) -> list[str]:
    """What's wrong with the details line's place (prompts/punchup.md "The details line"), []
    if nothing: the @details paragraph (the parser checks there's exactly one) comes after the
    paragraph with the plain explanation (explanation(); paragraph 1 without one) and before
    the one with the first sentence of the draft's paragraph 3 (the beat after it: who can
    apply, the conditions)."""
    at = next((i for i, p in enumerate(script.paragraphs) if p.details), None)
    if at is None:
        return []
    line = script.paragraphs[at].display
    problems = []
    if draft:
        sentence = explanation(draft)
        home = _home(script, sentence, conf().steps.script.changed_ratio) if sentence else 0
        if home is not None and at <= home:
            what = f"the plain explanation „{sentence}“" if sentence else "paragraph 1"
            problems.append(f"the details line „{line}“ goes after {what}, in a paragraph of its own")
        paragraphs = draft_paragraphs(draft)
        nxt = (sentences(paragraphs[2]) or [None])[0] if len(paragraphs) > 2 else None
        where = _home(script, nxt, conf().steps.script.changed_ratio) if nxt else None
        if where is not None and at > where:
            problems.append(f"the details line „{line}“ goes before the conditions, before „{nxt}“")
    return problems


def share_problems(script: Script, draft: str | None) -> list[str]:
    """What's wrong with the share line's place (prompts/punchup.md "The share line"), [] if
    nothing: the @share paragraph (the parser checks there's exactly one, not next to the
    details line) comes after the one holding the first sentence of the draft's paragraph 3
    (the conditions begin) and starts in the second half of the spoken words (the tag left
    out); it holds none of the draft's sentences (it's left out of display_text(), so one
    there would skip the draft lock and the fact-check). It may be the closing jab."""
    at = next((i for i, p in enumerate(script.paragraphs) if p.share), None)
    if at is None:
        return []
    line = script.paragraphs[at].display
    problems = []
    body = [p for p in script.paragraphs if not p.tag]
    before = sum(spoken_words(p.display) for p in script.paragraphs[:at])
    if before * 2 < sum(spoken_words(p.display) for p in body):
        problems.append(f"the share line „{line}“ goes in the second half of the script, after the conditions")
    if draft:
        paragraphs = draft_paragraphs(draft)
        nxt = (sentences(paragraphs[2]) or [None])[0] if len(paragraphs) > 2 else None
        where = _home(script, nxt, conf().steps.script.changed_ratio) if nxt else None
        if where is not None and at < where:
            problems.append(f"the share line „{line}“ goes after the conditions, after „{nxt}“")

        def close(a, b):
            return SequenceMatcher(None, normalize(a), normalize(b)).ratio() >= conf().steps.script.changed_ratio

        rest = sentences(display_text(script))
        kept = [
            d
            for d in sentences(draft)
            if any(close(d, s) for s in sentences(line)) and not any(close(d, s) for s in rest)
        ]
        if kept:
            problems.append(
                f"the share line's paragraph is the share line alone; „{kept[0]}“ is the story's: "
                "keep it in its own paragraph"
            )
    return problems


def _digits(text: str) -> set[str]:
    """The numbers in a display text, as digits (20 000 and 20,000 as 20000)."""
    return set(re.findall(r"\d+", _THOUSANDS.sub("", text)))


def _named(name: str, text: str) -> bool:
    """Whether a nominative Georgian name (or the domain) is in the text, in any case form:
    each word's stem (a final vowel dropped) starts a word of it."""
    words = [w.strip(".,!?:;„“\"'()").casefold() for w in text.split()]
    stems = [w[:-1] if len(w) > 3 and w[-1] in language.current().name_vowels else w for w in name.casefold().split()]
    return all(any(w.startswith(stem) for w in words) for stem in stems)


def _free(word: str) -> bool:
    stem = language.current().free_stem
    return bool(stem) and word.casefold().startswith(stem)


def stake_word(text: str) -> str | None:
    """The first word of `text` a fact graphic can show for sure: one with a digit, or free
    (the free word in any form); None without one. A place isn't looked for: a country's stem also
    starts other words (a country and its language)."""
    for word in text.split():
        bare = word.strip(".,!?:;„“\"'()")
        if any(ch.isdigit() for ch in bare) or _free(bare):
            return bare
    return None


def fx_problems(script: Script, *, every_fact: bool = True) -> list[str]:
    """The fact graphics' rules punch-up must keep (module docstring); [] if none broken.
    every_fact False: the straight style's card on every key fact (uncarded_facts()) left out."""
    from reelline.media.fx import config as fx_config
    from reelline.media.fx import countries, numbers_in

    reel = fx_config()["reel"]
    at = [(n, p) for n, p in enumerate(script.paragraphs, 1) if p.fx is not None]
    found = []
    if len(at) < reel["minPerScript"]:
        found.append(
            f"the script has {len(at)} @fx line(s); add {reel['minPerScript']}-{reel['maxPerReel']}, on the "
            "paragraphs that say the most visual facts"
        )
    stake = stake_word(script.paragraphs[0].display) if script.paragraphs[0].fx is None else None
    if stake:
        found.append(
            f"paragraph 1 says „{stake}“ but has no @fx: give it one with the stake its first sentence leads "
            "with (on screen from the first frame, no | <word>), and none on paragraph 2"
        )
    for n, p in at:
        fx, said = p.fx, _digits(p.display)
        missing = [x for x in numbers_in(fx.data) if x not in said]
        if missing:
            found.append(
                f"paragraph {n}: @fx {fx.raw} shows {', '.join(missing)}, which its text doesn't say in "
                "digits; the card shows only what its paragraph says"
            )
        names = {
            "location": [fx.data.get("city")],
            "apply": [fx.data.get("url")],
            "abroad": [k for k, v in countries().items() if v == countries().get(fx.data.get("destination"))],
        }
        if fx.template in names and not any(_named(x, p.display) for x in names[fx.template] if x):
            found.append(
                f"paragraph {n}: @fx {fx.raw}: its text doesn't name {fx.text}; put the @fx on the "
                "paragraph that says it"
            )
    if every_fact and reel_style.current().every_fact_fx:
        found += uncarded_facts(script)
    return found


# Sentences the draft doesn't have, at most this many quoted in a straight_problems() message.
_QUOTED = 3


def straight_problems(script: Script, draft: str | None) -> list[str]:
    """What breaks the straight style (reelline/media/style.py, prompts/straight.md), [] if nothing: an
    opening emotion, a sarcastic @style (Style.banned_styles), a laugh or tsk tag
    (Style.banned_tags), a sentence the draft doesn't have (draft_lock()'s inserted: a joke, a
    comment, a closing line; the details and share lines aren't in display_text())."""
    style = reel_style.current()
    found = []
    if script.opening is not None:
        found.append(
            f"no opening emotion: paragraph 1's @style is {script.opening}; make it an energetic nuance "
            "(energetic, excited, upbeat) or leave it out"
        )
    for n, p in enumerate(script.paragraphs, 1):
        banned = sorted({w for w in re.findall(r"[a-z]+", (p.style or "").lower()) if w in style.banned_styles})
        if banned:
            found.append(
                f"paragraph {n}: @style {p.style!r} is {', '.join(banned)}; no sarcasm: an energetic, "
                "clear nuance or none"
            )
        tags = sorted({t for t in _TAG.findall(p.tts) if t.strip("<>") in style.banned_tags})
        if tags:
            found.append(f"paragraph {n}: no {' '.join(tags)} (no jokes); only <short pause> or <gasp>")
    if draft:
        inserted = draft_lock(draft, script)["inserted"]
        if inserted:
            quoted = "; ".join(f"„{s}“" for s in inserted[:_QUOTED])
            found.append(
                f"no sentence of your own (a joke, a comment, a closing line): {quoted}; keep only the "
                "draft's sentences, the details line and the share line"
            )
    return found
