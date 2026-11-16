"""Text checks: markup, tag caption, source URL, platform limits and the credits line."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from reelline.config import conf
from reelline.media.script_markup import TAG_LINE
from reelline.pipeline.qa.base import QA, Check, Report, Subject, _dedup

_URL = re.compile(r"https?://\S+")


def markup_found(text: str) -> list[str]:
    """The leftover markup in `text`: QA["markup"] substrings and vocal tags."""
    found = [m for m in QA["markup"] if m in text]
    found += re.findall(QA["vocal_tag"], text)
    return _dedup(found)


def caption_chunks(reel_dir: Path) -> list[dict] | None:
    path = reel_dir / "captions.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def caption_words(reel_dir: Path) -> list[dict] | None:
    chunks = caption_chunks(reel_dir)
    return None if chunks is None else [w for c in chunks for line in c["lines"] for w in line]


def tag_captions(chunks: list[dict]) -> str:
    """The text of the last caption chunk(s): whole chunks from the end until they hold
    at least as many words as TAG_LINE, their words joined by single spaces."""
    need, words = len(TAG_LINE.split()), []
    for chunk in reversed(chunks):
        words[:0] = [w["text"] for line in chunk["lines"] for w in line]
        if len(words) >= need:
            break
    return " ".join(words)


def tag_caption_problems(chunks: list[dict]) -> list[str]:
    """The tag's captions must read exactly TAG_LINE, in chunks of their own (no other
    word in them)."""
    shown = tag_captions(chunks)
    return [] if shown == TAG_LINE else [f"the last caption chunk(s) show {shown!r}, not the tag {TAG_LINE!r}"]


def limit_problems(caption: str, title: str | None, hashtags: list[str] | None) -> tuple[list[str], dict]:
    """Over-limit lines and {field: {length, limit, platforms}} for the caption fields,
    each against the strictest steps.caption.limits entry that applies."""
    limits = conf().steps.caption.limits
    values = {"post caption": caption, "youtube title": title}
    problems, detail = [], {}
    for name, platforms in QA["caption_fields"].items():
        text = values[name]
        if text is None:
            continue
        limit = min(limits[p]["chars"] for p in platforms)
        strictest = [p for p in platforms if limits[p]["chars"] == limit]
        detail[name] = {"length": len(text), "limit": limit, "platforms": strictest}
        if len(text) > limit:
            problems.append(f"{name} is {len(text)} characters; the limit is {limit} ({', '.join(strictest)})")
    if hashtags is not None:
        cap = limits["instagram"]["hashtags"]
        detail["hashtags"] = {"count": len(hashtags), "limit": cap, "platforms": ["instagram"]}
        if len(hashtags) > cap:
            problems.append(f"{len(hashtags)} hashtags; the limit is {cap} (instagram)")
    return problems, detail


def url_status(url: str, client: httpx.Client) -> tuple[bool, str]:
    """(reachable, what happened): HEAD, then GET when HEAD isn't a success; the whole
    thing up to QA["url_attempts"] times."""
    last = ""
    for _ in range(QA["url_attempts"]):
        for method in ("HEAD", "GET"):
            try:
                r = client.request(method, url, timeout=QA["url_timeout_s"], follow_redirects=True)
            except httpx.HTTPError as exc:
                last = f"{method}: {type(exc).__name__}"
                continue
            if r.status_code < 400:
                return True, f"{method} {r.status_code}"
            last = f"{method} HTTP {r.status_code}"
    return False, last


def text_checks(s: Subject, http: httpx.Client) -> list[Check]:
    checks = []
    chunks = caption_chunks(s.reel_dir)
    if chunks is None:
        checks.append(Check("text", "caption markup", "fail", None, None, ["captions.json missing"]))
    else:
        words = [w for c in chunks for line in c["lines"] for w in line]
        bad = [(w["text"], m) for w in words for m in markup_found(w["text"])]
        checks.append(
            Check(
                "text",
                "caption markup",
                "fail" if bad else "pass",
                [b[0] for b in bad],
                QA["markup"],
                [f"leftover markup {m!r} in caption word {t!r}" for t, m in bad],
            )
        )
        problems = tag_caption_problems(chunks)
        checks.append(
            Check("text", "tag captions", "fail" if problems else "pass", tag_captions(chunks), TAG_LINE, problems)
        )
    if s.caption is None:
        checks.append(Check("text", "post caption", "skip", None, None, ["no post caption (no script for this reel)"]))
        return checks
    markup, limits, credits = caption_checks(
        s.caption, s.youtube_title, s.hashtags, s.reel_id, s.reel_dir / "images.json"
    )
    checks.append(markup)
    if s.source_url:
        ok, what = url_status(s.source_url, http)
        checks.append(
            Check(
                "text",
                "source url",
                "pass" if ok else "warn",
                what,
                "HTTP < 400",
                [] if ok else [f"source URL unreachable ({what}): {s.source_url}"],
            )
        )
    checks += [limits, credits]
    return checks


def credits_check(caption: str, reel_id: int | None, images_json: Path | None) -> Check:
    """The image credits line against the images the reel shows (reelline/pipeline/credits.py)."""
    from reelline.pipeline.credits import CreditsError, caption_problems, shown_images

    try:
        shown = shown_images(images_json)
    except CreditsError as exc:
        return Check("text", "credits line", "fail", None, None, [f"credits line: {exc}"])
    problems = caption_problems(caption, reel_id, shown)
    return Check(
        "text",
        "credits line",
        "fail" if problems else "pass",
        {"images_shown": len(shown)},
        {"max_line": conf().publish.credits_page.max_line},
        problems,
    )


def caption_checks(
    caption: str,
    title: str | None,
    hashtags: list[str] | None,
    reel_id: int | None = None,
    images_json: Path | None = None,
) -> list[Check]:
    """The post caption's markup, limit and credits line checks (a caption edit re-runs
    just these)."""
    bad = markup_found(_URL.sub("", caption))
    problems, detail = limit_problems(caption, title, hashtags)
    return [
        Check(
            "text",
            "post caption markup",
            "fail" if bad else "pass",
            bad,
            QA["markup"],
            [f"leftover markup {m!r} in the post caption" for m in bad],
        ),
        Check("text", "caption limits", "fail" if problems else "pass", detail, None, problems),
        credits_check(caption, reel_id, images_json),
    ]


def recheck_caption(
    path: Path | None,
    caption: str,
    title: str | None,
    hashtags: list[str] | None,
    reel_id: int | None = None,
    images_json: Path | None = None,
) -> Report:
    """Re-run the caption checks after a caption edit: the reel's last QA report (qa.json
    at `path`) with its caption checks replaced, written back. Without a qa.json (QA was
    skipped) the report has only the caption checks."""
    new = caption_checks(caption, title, hashtags, reel_id, images_json)
    data = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.is_file() else None
    old = [Check(**c) for c in data["reports"][-1]["checks"]] if data and data.get("reports") else []
    names = {c.name for c in new}
    report = Report([c for c in old if c.name not in names] + new)
    if data is not None:
        result = report.as_json()
        data["reports"].append({**result, "caption_recheck": True})
        data.update(status=result["status"], failures=result["failures"], warnings=result["warnings"])
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
