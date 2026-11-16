"""Script calls: brief -> story pass -> punch-up pass -> parser check (channel docs/scripts.md).

Submodules: base (constants, PassResult), text/story/openings (story pass), checks/facts/cards
(punch-up checks, fact cards), flirt/punchup/lock/recent (punch-up, draft lock, memories),
runs/chain/session (write_script), files/commands/trial/cli (files, command line).

story_pass() fills prompts/story.md with the brief and the hook examples ({{HOOK_EXAMPLES}}:
hook_examples(), data/hook_examples.json; left out without it). The model drafts three openings,
scores them on the prompt's rubric and opens with the best; the other two come back in an OPENINGS
block that split_openings() takes off the story into PassResult.openings (a missing or unreadable
block is only noted). The draft is checked by story_problems(): plain Georgian (no @ directives,
braces, vocal tags or em/en dashes, at least steps.script.story_min_georgian Georgian letters),
steps.script.story_words words, first sentence at most opening_max_words spoken words
(spoken_words()), paragraph 1 at most paragraph1_max_words, the plain explanation at most
explanation_max_words. A violation gets one follow-up turn naming the problems; a second fails.

punchup_pass() fills prompts/punchup.md with the brief, the draft, the recent lines and the
previous opening emotion, parses the answer with reelline/media/script_markup.parse(), and checks
it: opening_problems(), paragraph1_problems(), explanation_problems() (paragraph 1 and the plain
explanation are the draft's, unchanged), details_problems() (exactly one @details paragraph, after
the explanation and before the draft's paragraph 3; the last steps.script.recent_details lines
fill {{RECENT_DETAILS}}), share_problems() (exactly one @share paragraph in the second half, no
draft sentence; {{RECENT_SHARES}}) and fx_problems() (at least fx.json reel.minPerScript @fx, one on
paragraph 1 when it says a number or free, each card's numbers and place in its paragraph). A parser
error or a failed check gets one follow-up turn ("The parser rejected it: ..." / "The opening breaks
the rules: ..." etc., then "Output the corrected full script only."); a second one fails.

Both passes take an optional list of fact-check failures, appended to the prompt.

The flirt line: pick_flirt() rolls once per pick (FLIRT_PROBABILITY; picks.flirt) and its angle
(FLIRT_ANGLES; picks.flirt_angle); a pick for a slot that had an earlier pick takes that one's
values. Punch-up gets {{FLIRT}}: prompts/flirt_on.md with the angle and the last
steps.script.recent_flirts lines, plus prompts/flirt_no_eye_stalks.md when the banned-phrase rule
applies (banned_phrase_ok()); or prompts/flirt_off.md. The parser then requires exactly one @flirt
paragraph, or none. The flirt, details and share lines are left out of display_text(): the draft
lock, the fact-check, the jokes memory and the caption never see them.

Style (reelline/media/style.py STYLE_MODE, channel docs/scripts.md "Straight mode"): classic is the above.
Straight runs prompts/straight.md instead of punch-up: no jokes; {{RECENT_LINES}},
{{PREVIOUS_OPENING}} and {{FLIRT}} aren't in it, the story gets no hook examples, pick_flirt()
gives no flirt line, and the check adds straight_problems() and, in fx_problems(),
uncarded_facts(): every sentence saying a deadline, an age, an amount, free or a place
(key_facts()) sits in a paragraph whose card shows it. That is repaired in code on each answer
first (repair_cards()); the repaired answer passing every check is the answer
(PassResult.fx_repaired). On the last answer a fact still without its card is noted, not a failure.

draft_lock() compares the draft's sentences with the final script's display text, normalized,
fuzzily and in order: unchanged, changed or dropped per draft sentence, plus the final sentences
that match none (inserted: punch-up's jokes). A warning for the review, never a failure.

The jokes memory: save_jokes() stores a script's inserted sentences (the jokes table);
recent_lines() fills {{RECENT_LINES}} with the jokes of the last steps.script.recent_days days,
newest first, at most recent_max, only from scripts whose status is in recent_statuses.

write_script() runs the chain for a pick and stores it (scripts table): brief, story, punch-up,
proofread (reelline/pipeline/proofread.py), draft lock, fact-check (reelline/pipeline/factcheck.py;
inserted sentences are checked too, jokes about the viewer, groups or the host exempt). Failures get
one repair round: failures only in punch-up's inserted sentences re-run punch-up with them listed;
any in a draft sentence re-run story with them, then punch-up. A second fact-check: passing is
"ok", failing again is "flagged" (it still goes to review with a warning). A repair round that
itself fails keeps the first version, flagged. Then the end card's facts (reelline/pipeline/endcard.py
make_card(); scripts.passes["card"]), written by finish() as <script>.endcard.json, and the fact
graphics' fact-check (reelline/pipeline/fx.py check_fx(); scripts.passes["fx"], <script>.fx.json).
A pass failing twice, or a failed call, leaves the script "failed" with the error.

Each pass's output is stored in scripts.passes as soon as it's done. A retried or resumed job goes
on with the pick's unfinished script (unfinished(): running, failed, or written without its file,
on the same brief) and runs only the passes it doesn't have. A connection that dropped on every try
(reelline/providers/retry.py TransportFailure) fails the script and goes up as it is. complete()
writes the caption, then the file; the pick becomes "scripted" only with both.
"""

from __future__ import annotations

from reelline.paths import REPO_ROOT
from reelline.pipeline.script.base import (
    _DASHES,
    _EXAMPLES_LINE,
    _FENCE,
    _LETTER_FIRST,
    _NUMBER,
    _OPENINGS_LINE,
    _SENTENCE_END,
    _TAG,
    _THOUSANDS,
    _UNIT_SIGNS,
    ALT_OPENINGS,
    ENDCARD_PLACEHOLDERS,
    FLIRT_ON_PLACEHOLDERS,
    NO_PREVIOUS_OPENING,
    NO_RECENT_DETAILS,
    NO_RECENT_FLIRTS,
    NO_RECENT_LINES,
    NO_RECENT_SHARES,
    OPENING_RUBRIC,
    OPENINGS_MARKER,
    PROOFREAD_PLACEHOLDERS,
    PUNCHUP_PLACEHOLDERS,
    STORY_PLACEHOLDERS,
    STRAIGHT_OPENING,
    STRAIGHT_PLACEHOLDERS,
    PassResult,
    ScriptError,
    _today,
    _with_failures,
)
from reelline.pipeline.script.cards import (
    _blocks,
    _cut,
    _shown,
    _split_block,
    deadline_card,
    repair_cards,
    uncarded_notes,
)
from reelline.pipeline.script.chain import (
    write_script,
)
from reelline.pipeline.script.checks import (
    _QUOTED,
    _digits,
    _home,
    _named,
    details_problems,
    explanation_problems,
    fx_problems,
    opening_problems,
    paragraph1_problems,
    share_problems,
    stake_word,
    straight_problems,
)
from reelline.pipeline.script.cli import (
    PICK_SLOTS,
    USAGE,
    cmd_caption,
    cmd_card,
    cmd_recent,
    cmd_send,
    main,
)
from reelline.pipeline.script.commands import (
    cmd_run,
    cmd_show,
)
from reelline.pipeline.script.facts import (
    _CLAUSE_PUNCT,
    _MONEY_STEMS,
    _MONTH_STEMS,
    _NOT_PLACES,
    _NOTICE_STEMS,
    _PLACE_ENDINGS,
    _SCALE_STEMS,
    FACT_CARDS,
    FACT_NAMES,
    Uncarded,
    _bare,
    _place_forms,
    fact_key,
    key_facts,
    uncarded,
    uncarded_facts,
)
from reelline.pipeline.script.files import (
    calls_for,
    card_text,
    complete,
    cost_lines,
    factcheck_summary,
    finish,
    fx_text,
    openings_text,
    script_file,
    status_mark,
    write_card_file,
)
from reelline.pipeline.script.flirt import (
    flirt_block,
    has_banned_phrase,
    pick_flirt,
    previous_opening,
)
from reelline.pipeline.script.lock import (
    details_line,
    display_text,
    draft_lock,
    flirt_line,
    normalize,
    sentences,
    share_line,
)
from reelline.pipeline.script.openings import (
    _openings,
    _score,
    _scored,
    openings_note,
    split_openings,
)
from reelline.pipeline.script.punchup import (
    punchup_pass,
)
from reelline.pipeline.script.recent import (
    _recent,
    banned_phrase_ok,
    details_block,
    recent_details,
    recent_flirt_lines,
    recent_flirts,
    recent_jokes,
    recent_lines,
    recent_shares,
    save_jokes,
    shares_block,
)
from reelline.pipeline.script.runs import (
    ScriptRun,
    Version,
    _brief_for,
    _pass_json,
    _update,
    check_pick,
    stored_run,
    unfinished,
)
from reelline.pipeline.script.session import (
    CLAUDE_ROLES,
    PassesStore,
    _versions,
    session_steps,
    unchecked,
)
from reelline.pipeline.script.story import (
    _example,
    fill_story,
    hook_examples,
    story_pass,
)
from reelline.pipeline.script.text import (
    _number_words,
    _same,
    _whole_words,
    draft_paragraphs,
    explanation,
    georgian_share,
    spoken_words,
    story_problems,
    strip_fence,
)
from reelline.pipeline.script.trial import (
    cmd_trial,
    trial,
    trial_text,
)

__all__ = [
    "ALT_OPENINGS",
    "CLAUDE_ROLES",
    "ENDCARD_PLACEHOLDERS",
    "FACT_CARDS",
    "FACT_NAMES",
    "FLIRT_ON_PLACEHOLDERS",
    "NO_PREVIOUS_OPENING",
    "NO_RECENT_DETAILS",
    "NO_RECENT_FLIRTS",
    "NO_RECENT_LINES",
    "NO_RECENT_SHARES",
    "OPENINGS_MARKER",
    "OPENING_RUBRIC",
    "PICK_SLOTS",
    "PROOFREAD_PLACEHOLDERS",
    "PUNCHUP_PLACEHOLDERS",
    "REPO_ROOT",
    "STORY_PLACEHOLDERS",
    "STRAIGHT_OPENING",
    "STRAIGHT_PLACEHOLDERS",
    "USAGE",
    "_CLAUSE_PUNCT",
    "_DASHES",
    "_EXAMPLES_LINE",
    "_FENCE",
    "_LETTER_FIRST",
    "_MONEY_STEMS",
    "_MONTH_STEMS",
    "_NOTICE_STEMS",
    "_NOT_PLACES",
    "_NUMBER",
    "_OPENINGS_LINE",
    "_PLACE_ENDINGS",
    "_QUOTED",
    "_SCALE_STEMS",
    "_SENTENCE_END",
    "_TAG",
    "_THOUSANDS",
    "_UNIT_SIGNS",
    "PassResult",
    "PassesStore",
    "ScriptError",
    "ScriptRun",
    "Uncarded",
    "Version",
    "_bare",
    "_blocks",
    "_brief_for",
    "_cut",
    "_digits",
    "_example",
    "_home",
    "_named",
    "_number_words",
    "_openings",
    "_pass_json",
    "_place_forms",
    "_recent",
    "_same",
    "_score",
    "_scored",
    "_shown",
    "_split_block",
    "_today",
    "_update",
    "_versions",
    "_whole_words",
    "_with_failures",
    "banned_phrase_ok",
    "calls_for",
    "card_text",
    "check_pick",
    "cmd_caption",
    "cmd_card",
    "cmd_recent",
    "cmd_run",
    "cmd_send",
    "cmd_show",
    "cmd_trial",
    "complete",
    "cost_lines",
    "deadline_card",
    "details_block",
    "details_line",
    "details_problems",
    "display_text",
    "draft_lock",
    "draft_paragraphs",
    "explanation",
    "explanation_problems",
    "fact_key",
    "factcheck_summary",
    "fill_story",
    "finish",
    "flirt_block",
    "flirt_line",
    "fx_problems",
    "fx_text",
    "georgian_share",
    "has_banned_phrase",
    "hook_examples",
    "key_facts",
    "main",
    "normalize",
    "opening_problems",
    "openings_note",
    "openings_text",
    "paragraph1_problems",
    "pick_flirt",
    "previous_opening",
    "punchup_pass",
    "recent_details",
    "recent_flirt_lines",
    "recent_flirts",
    "recent_jokes",
    "recent_lines",
    "recent_shares",
    "repair_cards",
    "save_jokes",
    "script_file",
    "sentences",
    "session_steps",
    "share_line",
    "share_problems",
    "shares_block",
    "split_openings",
    "spoken_words",
    "stake_word",
    "status_mark",
    "stored_run",
    "story_pass",
    "story_problems",
    "straight_problems",
    "strip_fence",
    "trial",
    "trial_text",
    "uncarded",
    "uncarded_facts",
    "uncarded_notes",
    "unchecked",
    "unfinished",
    "write_card_file",
    "write_script",
]
