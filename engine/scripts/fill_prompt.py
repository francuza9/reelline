"""Fill a script prompt by hand, for testing a pass in claude.ai before Phase 1.

Usage:
  python scripts/fill_prompt.py story --brief BRIEF [--copy]
  python scripts/fill_prompt.py punchup --brief BRIEF --draft DRAFT [--recent RECENT] [--previous EMOTION] [--flirt] [--copy]
  python scripts/fill_prompt.py straight --brief BRIEF --draft DRAFT [--copy]

Prints the filled prompt (prompts/story.md, prompts/punchup.md or prompts/straight.md, the straight
style's second pass instead of punch-up: reelline/media/style.py; paths in the channel config). BRIEF, DRAFT and RECENT are UTF-8 files: the news brief, the
story pass's output, and the recently used lines (one per line). Without --recent,
{{RECENT_LINES}} is "(none yet)"; without --previous (the previous video's opening
emotion), {{PREVIOUS_OPENING}} is "(none)"; {{RECENT_DETAILS}} and {{RECENT_SHARES}} are always "(none yet)".
straight takes no recent lines, previous emotion or flirt line. The story's {{HOOK_EXAMPLES}} come from
data/hook_examples.json as in the chain (reelline/pipeline/script.py hook_examples(); left out without it, or in the
straight style). --copy puts the prompt on the clipboard instead of
printing it (clip.exe in WSL, else pbcopy / wl-copy / xclip / xsel).

Fails for a missing or empty file, and for any {{...}} placeholder left unfilled.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from reelline.config import conf
from reelline.media.prompts import PromptError, fill, read_value
from reelline.media.script_markup import OPENING_EMOTIONS
from reelline.pipeline.dates import georgian_day
from reelline.pipeline.script import (
    NO_PREVIOUS_OPENING,
    NO_RECENT_DETAILS,
    NO_RECENT_LINES,
    NO_RECENT_SHARES,
    fill_story,
    flirt_block,
    hook_examples,
)

# Clipboard tools outside WSL, first found wins; each reads UTF-8 on stdin.
CLIPBOARD_TOOLS = (
    ["pbcopy"],
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
)


def filled_prompt(
    pass_name: str,
    brief: Path,
    draft: Path | None = None,
    recent: Path | None = None,
    previous: str | None = None,
    publish_date: date | None = None,
    flirt: bool = False,
) -> str:
    day = georgian_day(publish_date or date.today())
    if pass_name == "story":
        return fill_story(
            conf().steps.script.story_prompt.read_text(encoding="utf-8"),
            read_value(brief),
            day,
            hook_examples()[0],
            conf().steps.script.story_prompt.name,
        )
    if draft is None:
        raise PromptError(f"{pass_name} needs --draft")
    if pass_name == "straight":
        values = {
            "NEWS_BRIEF": read_value(brief),
            "DRAFT": read_value(draft),
            "PUBLISH_DATE": day,
            "RECENT_DETAILS": NO_RECENT_DETAILS,
            "RECENT_SHARES": NO_RECENT_SHARES,
        }
        return fill(
            conf().steps.script.straight_prompt.read_text(encoding="utf-8"),
            values,
            conf().steps.script.straight_prompt.name,
        )
    values = {
        "NEWS_BRIEF": read_value(brief),
        "DRAFT": read_value(draft),
        "RECENT_LINES": read_value(recent) if recent is not None else NO_RECENT_LINES,
        "PREVIOUS_OPENING": previous or NO_PREVIOUS_OPENING,
        "PUBLISH_DATE": day,
        "FLIRT": flirt_block(flirt, angle=conf().steps.script.flirt_angles[0] if flirt else None),
        "RECENT_DETAILS": NO_RECENT_DETAILS,
        "RECENT_SHARES": NO_RECENT_SHARES,
    }
    return fill(
        conf().steps.script.punchup_prompt.read_text(encoding="utf-8"), values, conf().steps.script.punchup_prompt.name
    )


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def copy_to_clipboard(text: str) -> str:
    """Put `text` on the clipboard; returns the tool used."""
    if is_wsl():
        # clip.exe reads stdin in the console code page unless it starts with a
        # UTF-16 BOM; UTF-8 would mangle the Georgian.
        cmd, data = ["clip.exe"], text.encode("utf-16")
        if shutil.which(cmd[0]) is None:
            raise PromptError("clip.exe not found on PATH (WSL interop off?)")
    else:
        cmd = next((c for c in CLIPBOARD_TOOLS if shutil.which(c[0])), None)
        if cmd is None:
            raise PromptError("No clipboard tool found; install one of: " + ", ".join(c[0] for c in CLIPBOARD_TOOLS))
        data = text.encode("utf-8")
    result = subprocess.run(cmd, input=data, capture_output=True)
    if result.returncode != 0:
        raise PromptError(
            f"{cmd[0]} failed (exit {result.returncode}): {result.stderr.decode(errors='replace').strip()}"
        )
    return cmd[0]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="pass_name", required=True)
    story = sub.add_parser("story", help=f"fill {conf().steps.script.story_prompt.name}")
    punchup = sub.add_parser("punchup", help=f"fill {conf().steps.script.punchup_prompt.name}")
    straight = sub.add_parser(
        "straight", help=f"fill {conf().steps.script.straight_prompt.name} (the straight style's punch-up)"
    )
    for p in (story, punchup, straight):
        p.add_argument("--brief", type=Path, required=True, help="news brief (UTF-8 file)")
    for p in (punchup, straight):
        p.add_argument("--draft", type=Path, required=True, help="the story pass's output (UTF-8 file)")
    punchup.add_argument(
        "--recent", type=Path, default=None, help=f"recently used lines (UTF-8 file; default: {NO_RECENT_LINES})"
    )
    punchup.add_argument("--flirt", action="store_true", help="this video gets a flirt line (default: none)")
    punchup.add_argument(
        "--previous",
        choices=OPENING_EMOTIONS,
        default=None,
        help=f"the previous video's opening emotion (default: {NO_PREVIOUS_OPENING})",
    )
    for p in (story, punchup, straight):
        p.add_argument(
            "--publish-date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help="the day the video is published (default: today)",
        )
        p.add_argument("--copy", action="store_true", help="put the prompt on the clipboard instead of printing it")
    args = parser.parse_args(argv)

    try:
        text = filled_prompt(
            args.pass_name,
            args.brief,
            getattr(args, "draft", None),
            getattr(args, "recent", None),
            getattr(args, "previous", None),
            args.publish_date,
            getattr(args, "flirt", False),
        )
        if args.copy:
            tool = copy_to_clipboard(text)
            print(
                f"Copied the {args.pass_name} prompt ({len(text)} chars) to the clipboard with {tool}", file=sys.stderr
            )
        else:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
    except PromptError as exc:
        sys.exit(f"FAIL: {exc}")


if __name__ == "__main__":
    main()
