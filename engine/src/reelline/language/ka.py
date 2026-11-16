"""The Georgian language pack: month and weekday forms, the stems the fact checks look for, the fx value hints
and the script's letters. Data only; the engine reads it through reelline.language.current()."""

from __future__ import annotations

import re

from reelline.language.base import Language

MONTH_FORMS: dict[int, tuple[str, ...]] = {
    1: ("იანვარი", "იანვრის", "იანვარს", "იანვ", "იან"),
    2: ("თებერვალი", "თებერვლის", "თებერვალს", "თებ"),
    3: ("მარტი", "მარტის", "მარტს", "მარ"),
    4: ("აპრილი", "აპრილის", "აპრილს", "აპრ"),
    5: ("მაისი", "მაისის", "მაისს", "მაი"),
    6: ("ივნისი", "ივნისის", "ივნისს", "ივნ"),
    7: ("ივლისი", "ივლისის", "ივლისს", "ივლ"),
    8: ("აგვისტო", "აგვისტოს", "აგვ"),
    9: ("სექტემბერი", "სექტემბრის", "სექტემბერს", "სექტ", "სექ"),
    10: ("ოქტომბერი", "ოქტომბრის", "ოქტომბერს", "ოქტ"),
    11: ("ნოემბერი", "ნოემბრის", "ნოემბერს", "ნოემ", "ნოე"),
    12: ("დეკემბერი", "დეკემბრის", "დეკემბერს", "დეკ"),
}

WEEKDAYS = ("ორშაბათი", "სამშაბათი", "ოთხშაბათი", "ხუთშაბათი", "პარასკევი", "შაბათი", "კვირა")

MONTH_STEMS = (
    "იანვ",
    "თებერვ",
    "მარტ",
    "აპრილ",
    "მაის",
    "ივნის",
    "ივლის",
    "აგვისტ",
    "სექტემბ",
    "ოქტომბ",
    "ნოემბ",
    "დეკემბ",
)
MONEY_STEMS = ("ლარ", "დოლარ", "ევრო", "აშშ")
SCALE_STEMS = ("ათას", "მილიონ")
PLACE_ENDINGS = ("", "ში", "ის", "ს", "იდან", "დან", "ზე", "ად", "ით", "მდე", "ისკენ", "კენ")
# Country names that are also everyday Georgian words (განა: "really?", ჩადი: "go down"): not looked for.
NOT_PLACES = frozenset({"განა", "ჩადი", "მალი"})
# A date in a clause with one of these is when you hear back, not a deadline: შეგატყობინებენ (they'll
# notify you), გაცნობებენ, ეცნობებათ, გამოცხადდება / გამოაცხადებენ (announced), შედეგები (results),
# გამოვლინდება (revealed), გამოქვეყნდება (published), ცნობილი გახდება, გაიგებ (you'll find out).
NOTICE_STEMS = (
    "ტყობინ",
    "აცნობებ",
    "ეცნობებ",
    "გამოცხად",
    "გამოაცხად",
    "შედეგ",
    "გამოვლინდ",
    "გამოქვეყნ",
    "ცნობილ",
    "გაიგებ",
)

FX_VALUE_FORMS = {
    "deadline": "<day> <month>: 20 ოქტომბერი",
    "start": "<day> <month>: 9 ნოემბერი",
    "age": "<from>-<to> <unit>: 18-29 წლის",
    "money": "<amount> <unit>: 420 ლარი (digits only, no spaces)",
    "places": "<count> <unit>: 300 ადგილი",
    "duration": "<count> <unit>: 12 კვირა",
    "condition": "<one short line>: B2 ინგლისური",
    "free": "(no value)",
    "certificate": "(no value), or the document's name: დიპლომი",
    "prize": "<the prize>: 5000 ლარი, 1-ლი ადგილი",
    "location": "<a city in Georgia, nominative>: ქუთაისი",
    "abroad": "<a country, nominative>: გერმანია",
    "apply": "<a bare domain>: edu.aris.ge",
    "format": "ონლაინ or ადგილზე",
    "steps": "<2-4 labels, 1-2 words each, between slashes>: განაცხადი / ტესტი / გასაუბრება",
}

MONTH_NAMES = (
    "იანვარი",
    "თებერვალი",
    "მარტი",
    "აპრილი",
    "მაისი",
    "ივნისი",
    "ივლისი",
    "აგვისტო",
    "სექტემბერი",
    "ოქტომბერი",
    "ნოემბერი",
    "დეკემბერი",
)

FX_FORMATS = {"ონლაინ": "online", "ადგილზე": "onsite"}


def is_script_char(ch: str) -> bool:
    """A Georgian letter (Mkhedruli, Asomtavruli): what the share and language checks count."""
    return "Ⴀ" <= ch <= "ჿ" or "Ა" <= ch <= "Ჿ"


def is_script_char_wide(ch: str) -> bool:
    """Also Nuskhuri: what fx values and directive text accept."""
    return is_script_char(ch) or "ⴀ" <= ch <= "⴯"


def _whole_words(n: int) -> int:
    """The words a whole number is said in: up to 99 one (ოცდაერთი), 420 two (ოთხას ოცი), 1000 one (ათასი),
    2026 three (ორი ათას ოცდაექვსი)."""
    if n < 100:
        return 1
    if n < 1000:
        return 1 + (n % 100 > 0)
    for size in (10**12, 10**9, 10**6, 10**3):  # ტრილიონი, მილიარდი, მილიონი, ათასი
        if n >= size:
            high, rest = divmod(n, size)
            return (_whole_words(high) if high > 1 else 0) + 1 + (_whole_words(rest) if rest else 0)
    raise AssertionError(n)


RELATIVE_DAY_WORDS = frozenset({"დღეს", "დღესვე", "დღეიდან", "ზეგ", "ამაღამ", "გუშინწინ"})
RELATIVE_DAY_STEMS = ("ხვალ", "გუშინ", "დღევანდელ")
RELATIVE_DAY_PHRASES = (("ამ", "კვირ"),)  # ამ კვირაში, ამ კვირის
MISSING_FACT_WORDS = ("უცნობია", "არ არის მითითებული", "მითითებული არ არის")
MOUTH_LETTERS = {
    "D": "ა",
    "C": "ეკგქყღხჰ",
    "B": "ისზცძწჭჩჯშჟდტთნრ",
    "E": "ო",
    "F": "უ",
    "A": "ბპმფ",
    "G": "ვ",
    "H": "ლ",
}


KA = Language(
    code="ka",
    month_forms=MONTH_FORMS,
    weekdays=WEEKDAYS,
    year_word="წლის",
    letter_class="ა-ჰ",
    script_tail=re.compile(r"[Ⴀ-ჿ]+$"),
    is_script_char=is_script_char,
    is_script_char_wide=is_script_char_wide,
    whole_words=_whole_words,
    decimal_word_count=1,  # მთელი
    time_words=2,  # 19:00 = საღამოს შვიდი
    month_stems=MONTH_STEMS,
    money_stems=MONEY_STEMS,
    scale_stems=SCALE_STEMS,
    place_endings=PLACE_ENDINGS,
    not_places=NOT_PLACES,
    notice_stems=NOTICE_STEMS,
    free_stem="უფასო",
    years_stem="წლ",
    places_stem="ადგილ",
    name_vowels="აეიოუ",
    until_ending="მდე",
    relative_day_words=RELATIVE_DAY_WORDS,
    relative_day_stems=RELATIVE_DAY_STEMS,
    relative_day_phrases=RELATIVE_DAY_PHRASES,
    missing_fact_words=MISSING_FACT_WORDS,
    mouth_letters=MOUTH_LETTERS,
    lipsync_align=True,
    fx_value_forms=FX_VALUE_FORMS,
    fx_unit_hint="one Georgian word of at most {n} letters (ლარი, კვირა, ადგილი)",
    fx_location_hint="a city in Georgia, in Georgian letters, nominative (ქუთაისი)",
    fx_abroad_hint="a country by its Georgian name in the nominative (გერმანია, not გერმანიაში; no cities)",
    date_example="9 ოქტომბერს",
    fx_month_names=MONTH_NAMES,
    fx_formats=FX_FORMATS,
)
