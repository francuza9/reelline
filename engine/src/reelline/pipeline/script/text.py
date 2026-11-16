"""Text measures and the story draft's checks: Georgian share, spoken words, fences, draft paragraphs, story_problems()."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from reelline import language
from reelline.config import conf
from reelline.media.script_markup import (
    GROUP,
    relative_day_words,
)
from reelline.pipeline.script.base import _DASHES, _FENCE, _LETTER_FIRST, _NUMBER, _TAG, _THOUSANDS, _UNIT_SIGNS
from reelline.pipeline.script.lock import normalize, sentences


def georgian_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if language.current().is_script_char(ch)) / len(letters)


def _whole_words(n: int) -> int:
    return language.current().whole_words(n)


def _number_words(number: str) -> int:
    """A number's words: a time (19:00, "<evening> <seven>") two, a decimal (5.6, "<five> <whole> <six>")
    both sides and the whole word, else its parts' (a date 23.10.2026)."""
    pack = language.current()
    if ":" in number:
        return pack.time_words
    parts = re.split(r"[.,]", number)
    if len(parts) == 2:
        return pack.whole_words(int(parts[0])) + pack.decimal_word_count + pack.whole_words(int(parts[1]))
    return sum(pack.whole_words(int(part)) for part in parts)


def spoken_words(text: str) -> int:
    """How many words the voice says for `text`: markup isn't counted (vocal tags are
    dropped, a {display|spoken} group counts its spoken side), a number counts as the
    words it's said in (_number_words(): 420 is two words in Georgian), a word before it in the
    same token as one more (GPT-5), and each of _UNIT_SIGNS as one. A token with none of
    these and no letter (a lone hyphen) isn't a word."""
    plain = _THOUSANDS.sub("", _TAG.sub(" ", GROUP.sub(r"\2", text)))
    count = 0
    for token in plain.split():
        numbers = _NUMBER.findall(token)
        count += sum(token.count(sign) for sign in _UNIT_SIGNS)
        if numbers:
            count += sum(_number_words(n) for n in numbers) + bool(_LETTER_FIRST.match(token))
        else:
            count += any(ch.isalpha() for ch in token)
    return count


def story_problems(text: str) -> list[str]:
    problems = []
    if "@" in text:
        problems.append("it contains @ directives; write plain text only")
    if any(ch in text for ch in "{}|"):
        problems.append("it contains braces or | ({display|spoken} groups); write plain text only")
    if _TAG.search(text):
        problems.append("it contains <...> vocal tags; write plain text only")
    if any(d in text for d in _DASHES):
        problems.append("it contains em or en dashes; use hyphens (-) only")
    words = len(text.split())
    low, high = conf().steps.script.story_words
    if not low <= words <= high:
        problems.append(f"it is {words} words; it must be {low}-{high} words")
    first = (sentences(text) or [""])[0]
    spoken = spoken_words(first)
    if spoken > conf().steps.script.opening_max_words:
        problems.append(
            f"its first sentence is {spoken} words as spoken (a number counts as the words "
            f"it's said in); it must be at most {conf().steps.script.opening_max_words}: one idea, what this is and "
            f"what the viewer can get, the way you'd tell a friend, no label and no bureaucratic "
            f"phrasing: „{first}“"
        )
    paragraphs = draft_paragraphs(text)
    head = spoken_words(paragraphs[0]) if paragraphs else 0
    if head > conf().steps.script.paragraph1_max_words:
        problems.append(
            f"its paragraph 1 is {head} words as spoken; it must be at most "
            f"{conf().steps.script.paragraph1_max_words}: the first sentence and the open loop, nothing else: "
            f"„{paragraphs[0]}“"
        )
    plain = explanation(text)
    if plain is not None and spoken_words(plain) > conf().steps.script.explanation_max_words:
        problems.append(
            f"the plain explanation (the first sentence of paragraph 2) is {spoken_words(plain)} words as "
            f"spoken; it must be at most {conf().steps.script.explanation_max_words}: one short plain sentence of "
            f"what you'll do there and what you get, then straight on to the conditions: „{plain}“"
        )
    relative = relative_day_words(text)
    if relative:
        problems.append(
            f"it uses relative day words ({', '.join(dict.fromkeys(relative))}); the video is published "
            "on a later day: write the dates themselves"
        )
    share = georgian_share(text)
    if share < conf().steps.script.story_min_georgian:
        problems.append(
            f"only {share:.0%} of its letters are Georgian; write in Georgian (brand names may stay in Latin letters)"
        )
    return problems


def strip_fence(text: str) -> str:
    """The text inside one surrounding ``` fence, if the whole answer is fenced."""
    m = _FENCE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _same(a: str, b: str) -> bool:
    """Two sentences are the same, normalized (draft_lock()'s same_ratio)."""
    a, b = normalize(a), normalize(b)
    return a == b or SequenceMatcher(None, a, b).ratio() >= conf().steps.script.same_ratio


def draft_paragraphs(draft: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", draft.strip()) if p.strip()]


def explanation(draft: str) -> str | None:
    """The draft's plain explanation (prompts/story.md "Say what it is, in plain words"): the
    first sentence of its paragraph 2. None for a draft of one paragraph."""
    paragraphs = draft_paragraphs(draft)
    first = sentences(paragraphs[1])[:1] if len(paragraphs) > 1 else []
    return first[0] if first else None
