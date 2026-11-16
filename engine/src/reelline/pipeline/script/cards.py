"""The fact cards' repair in code: splitting paragraphs and giving a deadline its card."""

from __future__ import annotations

import re

from reelline import language
from reelline.media.script_markup import (
    BLANK_LINE,
    GROUP,
    Script,
)
from reelline.pipeline.script.base import _SENTENCE_END
from reelline.pipeline.script.checks import _digits
from reelline.pipeline.script.facts import _MONTH_STEMS, FACT_CARDS, FACT_NAMES, key_facts, uncarded
from reelline.pipeline.script.lock import sentences


def _blocks(text: str, script: Script) -> list[tuple[int, int]] | None:
    """The paragraphs' (start, end) in punch-up's answer as parse() splits it (the @cover block
    left out); None when they don't map onto script.paragraphs."""
    out, pos = [], 0
    for m in [*BLANK_LINE.finditer(text), None]:
        end = m.start() if m else len(text)
        if text[pos:end].strip():
            out.append((pos, end))
        pos = m.end() if m else pos
    if script.cover is not None:
        out = out[1:]
    return out if len(out) == len(script.paragraphs) else None


def _shown(raw: str) -> str:
    """A paragraph's text as its display text has it (reelline/media/script_markup.py parse(): whitespace
    collapsed, vocal tags and the whitespace before them dropped, a group's display side)."""
    plain = re.sub(r" +", " ", re.sub(r"\s*<[^<>]*>", "", re.sub(r"\s+", " ", raw))).strip()
    return GROUP.sub(lambda m: m.group(1), plain)


def _cut(raw: str, want: list[str], before: list[int]) -> list[str] | None:
    """A paragraph's text as written (`raw`: groups and vocal tags in, whitespace collapsed; its
    display sentences `want`) cut before each of its sentences `before` (indices into `want`,
    ascending); a vocal tag right after a cut stays with the sentence before it. None when a cut
    doesn't map onto the text (a group across a sentence end, say)."""
    pieces, start, todo = [], 0, list(before)
    for m in _SENTENCE_END.finditer(raw):
        if not todo:
            break
        if raw.count("{", 0, m.start()) > raw.count("}", 0, m.start()):
            continue
        if len(sentences(_shown(raw[: m.start()]))) == todo[0]:
            pieces.append(raw[start : m.start()])
            start, todo = m.end(), todo[1:]
    if todo:
        return None
    pieces.append(raw[start:])
    for j in range(1, len(pieces)):
        lead = re.match(r"(?:\s*<[^<>]*>)+\s*", pieces[j])
        if lead:
            pieces[j - 1] += " " + lead.group().strip()
            pieces[j] = pieces[j][lead.end() :]
    bounds = [0, *before, len(want)]
    if any(sentences(_shown(pc)) != want[a:b] for pc, a, b in zip(pieces, bounds, bounds[1:], strict=False)):
        return None
    return pieces


def deadline_card(facts: tuple[tuple[str, str], ...]) -> str | None:
    """The @fx value for a sentence's only key fact when it's for sure a deadline: one day said
    with -until (until: "20 <month>-until" -> deadline "20 <month>"); None otherwise (a date with no -until
    may be a start or an event, not a deadline; any other fact needs words the code can't pick)."""
    from reelline.media.fx import MONTHS, FxError, parse_value

    if len(facts) != 1 or facts[0][0] != "deadline":
        return None
    words = facts[0][1].split()
    if len(words) != 2 or not words[0].isdigit() or not words[1].endswith(language.current().until_ending):
        return None
    month = next((MONTHS[k] for k, stem in enumerate(_MONTH_STEMS) if words[1].startswith(stem)), None)
    try:
        parse_value("deadline", f"{words[0]} {month}")
    except FxError:
        return None
    return f"deadline {words[0]} {month}"


def _split_block(
    script: Script, i: int, block: str, conflicts: dict[int, tuple[tuple[str, str], ...]], new_card
) -> tuple[str, str] | None:
    """Paragraph i (0-based, its block as written) split so that its card's sentences and the
    sentences `conflicts` (index -> the facts the card doesn't show) are paragraphs of their own, at
    the sentence ends between them; (the new blocks, a note) or None when it can't be done.
    The @fx goes with its sentences: the part its anchor word is in, else the one with a fact it
    shows, else one saying its numbers, else the first without a conflict; an anchor left in
    another part is dropped (from the part's first word). @card and the first @image stay on the
    first part, @style goes on every part, a further @image goes to the part its anchor word is in
    (its first picture, if it's the first there: shown from the part's first word). A conflict
    part gets new_card(its facts) as its @fx when that's not None."""
    from reelline.media.fx import numbers_in
    from reelline.media.script_markup import fx_token, image_anchors

    p = script.paragraphs[i]
    want = sentences(p.display)
    flags = [k in conflicts for k in range(len(want))]
    runs = [[0]]
    for k in range(1, len(want)):
        if flags[k] == flags[k - 1]:
            runs[-1].append(k)
        else:
            runs.append([k])
    if len(runs) < 2:
        return None
    raw = re.sub(r"\s+", " ", " ".join(line for line in block.split("\n") if not line.strip().startswith("@"))).strip()
    pieces = _cut(raw, want, [r[0] for r in runs[1:]])
    if pieces is None or " ".join(_shown(pc) for pc in pieces) != p.display:
        return None
    first = script.first_token(i)
    offset = script.tokens[first].display_start
    starts = [0]
    for pc in pieces[:-1]:
        starts.append(starts[-1] + len(_shown(pc)) + 1)

    def part_of(token):
        return max(j for j, s in enumerate(starts) if s <= script.tokens[token].display_start - offset)

    clean = [j for j, r in enumerate(runs) if not flags[r[0]]]

    def shows(j):
        return any(p.fx.template in FACT_CARDS[kind] for k in runs[j] for kind, _ in key_facts(want[k]))

    numbers = numbers_in(p.fx.data)

    def says(j):
        return bool(numbers) and all(x in _digits(_shown(pieces[j])) for x in numbers)

    anchored = part_of(fx_token(script, i)) if p.fx.anchor else None
    owner = next(
        j for j in [anchored, *[j for j in clean if shows(j)], *[j for j in clean if says(j)], clean[0]] if j in clean
    )
    fx_line = f"@fx: {p.fx.raw if anchored == owner else f'{p.fx.template} {p.fx.value}'.strip()}"
    images: list[list[str]] = [[] for _ in pieces]
    if p.image is not None:
        images[0].append(f"@image: {p.image.raw}")
    for slot, token in zip(p.more_images, image_anchors(script, i), strict=False):
        j = part_of(token)
        images[j].append(f"@image: {slot.directive.raw}" + (f" | {slot.anchor}" if images[j] else ""))
    out, made = [], []
    for j, piece in enumerate(pieces):
        lines = [f"@style: {p.style}"] if p.style else []
        if j == 0 and p.card:
            lines.append(f"@card: {p.card}")
        lines += images[j]
        if j == owner:
            lines.append(fx_line)
        elif flags[runs[j][0]]:
            card = new_card(tuple(f for k in runs[j] for f in conflicts.get(k, ())))
            if card:
                lines.append(f"@fx: {card}")
                made.append(f"@fx: {card} on „{_shown(piece)}“")
        out.append("\n".join(lines + [piece]))
    note = (
        f"paragraph {i + 1} split before „{want[runs[1][0]]}“"
        if len(runs) == 2
        else f"paragraph {i + 1} split into {len(runs)} at its facts"
    )
    note += f": its {p.fx.template} card stays on „{want[runs[owner][0]]}“" + "".join(f"; added {m}" for m in made)
    return "\n\n".join(out), note


def repair_cards(text: str, script: Script) -> tuple[str, list[str]]:
    """Punch-up's answer `text` (parsed: `script`) with uncarded() repaired in code where that's
    sure (module docstring): a paragraph whose card doesn't show a sentence's fact is split
    between them (_split_block()); a paragraph with no card, or a part split off, gets a deadline
    card when its only new fact is a -until day (deadline_card()), never past fx.json
    reel.maxPerReel. Paragraph 1 (the opening's own rules) is left as it is. (the text, a note
    per change); (`text`, []) when nothing could be done."""
    from reelline.media.fx import config as fx_config

    found = uncarded(script)
    blocks = _blocks(text, script) if found else None
    if not blocks:
        return text, []
    room = int(fx_config()["reel"]["maxPerReel"]) - sum(p.fx is not None for p in script.paragraphs)

    def new_card(facts: tuple[tuple[str, str], ...]) -> str | None:
        nonlocal room
        card = deadline_card(facts) if room > 0 else None
        room -= card is not None
        return card

    edits, notes = [], []
    for i in sorted({u.paragraph for u in found} - {0}):
        start, end = blocks[i]
        mine = {u.index: u.facts for u in found if u.paragraph == i}
        if script.paragraphs[i].fx is None:
            card = new_card(tuple(f for facts in mine.values() for f in facts))
            if card:
                lines = text[start:end].split("\n")
                at = next(k for k, line in enumerate(lines) if line.strip() and not line.strip().startswith("@"))
                edits.append((start, end, "\n".join(lines[:at] + [f"@fx: {card}"] + lines[at:])))
                notes.append(f"paragraph {i + 1}: added @fx: {card}")
            continue
        split = _split_block(script, i, text[start:end], mine, new_card)
        if split:
            edits.append((start, end, split[0]))
            notes.append(split[1])
    for s, e, new in sorted(edits, reverse=True):
        text = text[:s] + new + text[e:]
    return text, notes


def uncarded_notes(script: Script) -> list[str]:
    """uncarded() for the log and the review: what's left without a card."""
    return [
        f"paragraph {u.paragraph + 1}'s „{u.sentence}“ says "
        + ", ".join(f"{FACT_NAMES[kind]} „{word}“" for kind, word in u.facts)
        + ", no card shows it"
        for u in uncarded(script)
    ]
