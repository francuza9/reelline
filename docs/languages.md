# Languages

`channel.language` names a *language pack* (`reelline/language/`): the words and forms that the engine's text checks and date parsing need. The shipped packs are `ka` (Georgian) and `en` (English). The pack does not translate prompts: the channel's `prompts/` are written in the channel's language.

## What a pack provides

A pack is a frozen `Language` dataclass (`language/base.py`) filled with data and small functions:

| Group | Fields | Used for |
|---|---|---|
| Dates | `month_forms`, `weekdays`, `year_word`, `letter_class` | reading dates from articles, and relative-day checks |
| Alphabet | `is_script_char`, `script_tail`, `is_script_char_wide` | what counts as the language's letters in a script or graphic |
| Length | `whole_words`, `time_words`, `decimal_word_count` | estimating spoken length |
| Key facts | `month_stems`, `money_stems`, `scale_stems`, `place_endings`, `not_places`, `notice_stems`, `free_stem`, `years_stem`, `places_stem`, `name_vowels`, `until_ending` | spotting the facts in a sentence (numbers, places, deadlines) so changes to them are caught |
| Banned and missing | `relative_day_words/stems/phrases`, `missing_fact_words` | relative days ("tomorrow") cannot be used in a script; words a model writes for a missing fact |
| Lip sync | `mouth_letters` | the letters of each Rhubarb mouth shape |
| Graphics | `fx_value_forms`, `fx_month_names`, `fx_formats`, `fx_unit_hint`, `fx_location_hint`, `fx_abroad_hint`, `date_example` | how fact-graphic values are written and what the model is told |
| Flags | `spoken_latin_ok`, `lipsync_align` | see below |

`spoken_latin_ok`: the TTS reads digits and Latin letters as they are (English), so scripts need no separate spoken form for them. `lipsync_align`: the language's letters can be aligned to the voice for lip sync and caption timing.

## Known limit

The forced aligner that times captions and lip sync (`scripts/align.py`) exists only for Georgian. A channel in another language therefore gets everything up to the script, the checks, the end card, the caption, the cover and stills of frames, but not yet a fully voiced and timed video. The English pack is minimal: a channel writing in English may need to fill in what its checks use (`fx_value_forms`, `notice_stems`).

## Adding a language

1. Copy `language/en.py` to `language/<code>.py`, fill in the fields, and add the code to `get()` in `language/__init__.py`.
2. Write the channel's prompts in that language (the `channels/example/prompts/` set is the template).
3. Set `channel.language: <code>` and, if your language needs it, add to `sources.yaml` entries with `lang: <code>`.
4. Run the tests and `reelline doctor`.

## Writing an error catalog

The checks that catch a script's mistakes are what the fact-check and the code guards rely on, and a good catalog of *the mistakes a model makes in your language* makes them sharper. A starter method:

1. Generate or collect a few dozen scripts and read them with a native speaker.
2. For each real mistake (a wrong case ending on a place name, a relative day, a number that reads differently spoken, a fact written for a missing one), write down the pattern: the word stem or ending that gives it away.
3. Put the patterns in the pack's fields: stems into the key-facts group, banned relative-day forms into the banned group, forms the TTS misreads into the script markup's `{display|spoken}` forms.
4. Turn each into a unit test with the sentence that failed, so it stays caught.

Keep entries to patterns that are wrong in every context; a catalog that rejects good sentences costs repairs.
