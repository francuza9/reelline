import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import align
from align import AlignError, _set_ends, romanize_letters


def letter(index, start, end):
    return {"char": "ა", "index": index, "start": start, "end": end}


def test_within_word_letter_ends_at_next_start():
    letters = [letter(0, 0.10, 0.12), letter(1, 0.30, 0.32)]
    _set_ends(letters, quiet=[False] * 50, duration=1.0)
    assert letters[0]["end"] == 0.30


def test_word_final_letter_ends_at_silence():
    # Loud until 0.20 s, quiet after; next word starts at 0.60 s.
    quiet = [False] * 10 + [True] * 40
    letters = [letter(0, 0.10, 0.12), letter(2, 0.60, 0.62)]
    _set_ends(letters, quiet, duration=1.0)
    assert letters[0]["end"] == pytest.approx(0.20)


def test_word_final_letter_capped_at_next_word():
    letters = [letter(0, 0.10, 0.12), letter(2, 0.30, 0.32)]
    _set_ends(letters, quiet=[False] * 50, duration=1.0)
    assert letters[0]["end"] == pytest.approx(0.30)
    assert letters[1]["end"] == pytest.approx(1.0)


def test_romanize_keeps_letter_positions():
    letters = romanize_letters("შე, ვარ!")
    assert [(ltr["char"], ltr["roman"], ltr["index"]) for ltr in letters] == [
        ("შ", "sh", 0),
        ("ე", "e", 1),
        ("ვ", "v", 4),
        ("ა", "a", 5),
        ("რ", "r", 6),
    ]


@pytest.mark.parametrize("text", ["მე 5 ვარ", "hello", "ჱ"])
def test_romanize_rejects_unalignable_characters(text):
    with pytest.raises(AlignError):
        romanize_letters(text)


def test_release_drops_the_cached_aligner():
    align._bundle()  # the pipeline object only; no model download
    align.release()
    assert align._bundle.cache_info().currsize == 0 and align._model.cache_info().currsize == 0
