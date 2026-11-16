"""@fx values: parsing a template's value strictly, and showing it as the card does."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from reelline import language
from reelline.media.fx.base import (
    ANCHOR_SEP,
    DOMAIN,
    FORMATS,
    FX_SUFFIX,
    MAX_TEXT_CHARS,
    MAX_UNIT_CHARS,
    MONTH_DAYS,
    MONTHS,
    NOT_ON_CARD,
    STEP_MAX_CHARS,
    STEP_MAX_WORDS,
    TEMPLATES,
    UNIT_SIGNS,
    VALUE_FORMS,
    FxError,
    _georgian,
    _georgian_word,
    config,
)
from reelline.media.fx.spec import countries


@dataclass(frozen=True)
class FxDirective:
    """A paragraph's @fx: the template, its data (remotion/src/types.ts FxData, without the
    deadline's daysLeft, which the render adds), the value as written (whitespace
    normalized) and the anchor word, or None for the paragraph's first word."""

    template: str
    data: dict
    value: str
    anchor: str | None = None

    @property
    def raw(self) -> str:
        """The directive's value as written: `<template> <value>[ | <anchor>]`."""
        head = f"{self.template} {self.value}".strip()
        return f"{head} {ANCHOR_SEP} {self.anchor}" if self.anchor else head

    @property
    def text(self) -> str:
        """What the card shows, as one line (the fact-check and the review read it)."""
        return show(self.data)


def _number(text: str, what: str) -> int:
    if not re.fullmatch(r"\d{1,7}", text) or text.startswith("0"):
        raise FxError(
            f"{what} must be a whole number written in digits, no spaces or separators (420, 20000), got {text!r}"
        )
    return int(text)


def _unit(text: str, what: str) -> str:
    if text in UNIT_SIGNS or (_georgian_word(text) and len(text) <= MAX_UNIT_CHARS):
        return text
    raise FxError(
        f"{what}'s unit is {language.current().fx_unit_hint.format(n=MAX_UNIT_CHARS)} "
        f"or one of {' '.join(UNIT_SIGNS)}, got {text!r}"
    )


def _date(words: list[str], template: str) -> dict:
    if len(words) != 2:
        raise FxError(f"{template} is <day> <month> ({VALUE_FORMS[template]}), got {' '.join(words)!r}")
    day, month = words
    if month not in MONTHS:
        raise FxError(
            f"{template}: the month is its Georgian name in the nominative ({', '.join(MONTHS)}), got {month!r}"
        )
    n = _number(day, f"{template}'s day")
    if not 1 <= n <= MONTH_DAYS[MONTHS.index(month)]:
        raise FxError(f"{template}: {month} has no day {n}")
    return {"day": n, "month": month}


def _text(value: str, template: str, max_chars: int) -> str:
    if not value:
        raise FxError(f"{template} needs its text ({VALUE_FORMS[template]})")
    if len(value) > max_chars:
        raise FxError(f"{template}'s text is {len(value)} characters, at most {max_chars}: {value!r}")
    bad = sorted({ch for ch in value if ch in NOT_ON_CARD})
    if bad:
        raise FxError(f"{template}: no markup or dashes other than a hyphen, found {''.join(bad)!r} in {value!r}")
    if not any(_georgian(ch) or ch.isdigit() for ch in value):
        raise FxError(f"{template}'s text must be Georgian (digits and brand names aside), got {value!r}")
    return value


def parse_value(template: str, value: str) -> dict:
    """An @fx template name and value -> the template's data (FxData); FxError if the template
    can't show it (module docstring)."""
    value = " ".join(value.split())
    words = value.split()
    if template not in TEMPLATES:
        raise FxError(f"unknown fx template {template!r}; one of: {', '.join(TEMPLATES)}")
    data: dict = {"template": template}
    if template in ("deadline", "start"):
        data.update(_date(words, template))
    elif template == "age":
        m = re.fullmatch(r"(\d{1,3})-(\d{1,3}) (\S+)", value)
        if m is None:
            raise FxError(f"age is <from>-<to> <unit> ({VALUE_FORMS['age']}), got {value!r}")
        lo, hi = int(m.group(1)), int(m.group(2))
        if not 0 < lo < hi <= 120:
            raise FxError(f"age: {lo}-{hi} isn't a range of ages (from below to, both 1-120)")
        data.update({"from": lo, "to": hi, "unit": _unit(m.group(3), "age")})
    elif template in ("money", "places", "duration"):
        if len(words) != 2:
            raise FxError(f"{template} is {VALUE_FORMS[template]}, got {value!r}")
        data.update({"value": _number(words[0], f"{template}'s amount"), "unit": _unit(words[1], template)})
    elif template == "condition":
        data["text"] = _text(value, template, int(config()["templates"]["condition"]["maxChars"]))
    elif template == "free":
        if value:
            raise FxError(
                f"free takes no value (the stamp reads {config()['templates']['free']['text']}), got {value!r}"
            )
    elif template == "certificate":
        if value:
            data["label"] = _text(value, template, MAX_TEXT_CHARS["certificate"])
    elif template == "prize":
        data["text"] = _text(value, template, MAX_TEXT_CHARS["prize"])
    elif template == "location":
        if not value or not all(_georgian_word(w.replace("-", "")) for w in words):
            raise FxError(f"location is {language.current().fx_location_hint}, got {value!r}")
        data["city"] = _text(value, template, MAX_TEXT_CHARS["location"])
    elif template == "abroad":
        if value not in countries():
            raise FxError(
                f"abroad is {language.current().fx_abroad_hint}, "
                f"as in remotion/src/fx/destinations.json; {value!r} isn't one"
            )
        data["destination"] = value
    elif template == "apply":
        if not DOMAIN.fullmatch(value) or value.startswith("www."):
            raise FxError(f"apply is a bare domain (edu.aris.ge: lowercase, no https://, www. or path), got {value!r}")
        data["url"] = value
    elif template == "format":
        if value not in FORMATS:
            raise FxError(f"format is {' or '.join(FORMATS)}, got {value!r}")
        data["mode"] = FORMATS[value]
    elif template == "steps":
        labels = [" ".join(s.split()) for s in value.split("/")]
        lo, hi = config()["templates"]["steps"]["minSteps"], config()["templates"]["steps"]["maxSteps"]
        if not lo <= len(labels) <= hi or not all(labels):
            raise FxError(f"steps are {lo}-{hi} labels between slashes ({VALUE_FORMS['steps']}), got {value!r}")
        for label in labels:
            if len(label.split()) > STEP_MAX_WORDS:
                raise FxError(f"steps: each label is 1-{STEP_MAX_WORDS} words, got {label!r}")
            _text(label, "steps", STEP_MAX_CHARS)
        data["labels"] = labels
    return data


def parse_directive(value: str) -> FxDirective:
    """An @fx line's value (`<template> <value> [| <anchor>]`) -> FxDirective; FxError otherwise."""
    value = " ".join(value.split())
    head, sep, anchor = value.partition(ANCHOR_SEP)
    head, anchor = head.strip(), anchor.strip()
    if sep and (not anchor or len(anchor.split()) != 1):
        raise FxError(f"the anchor after {ANCHOR_SEP!r} is one display word of the paragraph, got {anchor!r}")
    template, _, rest = head.partition(" ")
    if not template:
        raise FxError("empty @fx; write @fx: <template> <value>")
    return FxDirective(template, parse_value(template, rest.strip()), rest.strip(), anchor or None)


def show(data: dict) -> str:
    """The card's fact as one line of text."""
    t = data["template"]
    if t in ("deadline", "start"):
        return f"{data['day']} {data['month']}"
    if t == "age":
        return f"{data['from']}-{data['to']} {data['unit']}"
    if t in ("money", "places", "duration"):
        return f"{data['value']} {data['unit']}"
    if t in ("condition", "prize"):
        return data["text"]
    if t == "free":
        return config()["templates"]["free"]["text"]
    if t == "certificate":
        return data.get("label") or config()["templates"]["certificate"]["label"]
    if t == "location":
        return data["city"]
    if t == "abroad":
        return data["destination"]
    if t == "apply":
        return data["url"]
    if t == "format":
        return config()["templates"]["format"]["labels"][data["mode"]]
    return " / ".join(data["labels"])


def numbers_in(data: dict) -> list[str]:
    """The numbers the card shows, as digits (each must be in the paragraph's display text)."""
    t = data["template"]
    if t in ("deadline", "start"):
        return [str(data["day"])]
    if t == "age":
        return [str(data["from"]), str(data["to"])]
    if t in ("money", "places", "duration"):
        return [str(data["value"])]
    if t in ("condition", "prize"):
        return re.findall(r"\d+", data["text"])
    return []


def fx_path(script_path: Path) -> Path:
    """<name>.fx.json next to the script <name>.txt."""
    return script_path.with_suffix(FX_SUFFIX)


def write_checked(path: Path, shown: list[dict], dropped: list[dict], **meta) -> Path:
    """The fact-check's file: `shown` [{paragraph, raw, template, text}] the render may show,
    `dropped` [{paragraph, raw, template, text, reason}] it never shows; `meta` alongside."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"shown": shown, "dropped": dropped, **meta}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path
