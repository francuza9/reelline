"""The language packs (reelline/language): the channel's is the one channel.language names, a pack is looked up by
code, and the engine's language-specific checks read their data from it. The Georgian pack's own checks: test_language_ka.py."""

import pytest
from reelline import language
from reelline.config import ConfigError, conf
from reelline.pipeline.dates import MONTHS, WEEKDAYS


def test_the_channels_pack_is_the_configured_one():
    pack = language.current()
    assert pack.code == conf().channel.language and pack is language.get(conf().channel.language)
    assert MONTHS is pack.month_forms and pack.weekdays == WEEKDAYS


def test_unknown_language_fails_loudly():
    with pytest.raises(ConfigError, match="unknown language 'xx'"):
        language.get("xx")


def test_a_channel_names_its_pack_by_code(monkeypatch):
    for code in ("en", "ka"):
        monkeypatch.setattr(conf().channel, "language", code)
        assert language.current() is language.get(code) and language.current().code == code


def test_english_pack_has_no_georgian_data():
    en = language.get("en")
    assert en.month_forms[10] == ("October", "Oct", "Oct.") and en.weekdays[6] == "Sunday"
    assert en.is_script_char("a") and not en.is_script_char("\u10d0")  # Georgian letter a
    assert [en.whole_words(n) for n in (7, 25, 100, 1000)] == [1, 2, 2, 2]
    assert en.free_stem == "free" and not en.relative_day_words and not en.mouth_letters


def test_english_speaks_digits_and_latin_but_has_no_lip_sync_yet():
    from reelline.language import get

    en, ka = get("en"), get("ka")
    assert en.spoken_latin_ok and not ka.spoken_latin_ok
    assert ka.lipsync_align and not en.lipsync_align
