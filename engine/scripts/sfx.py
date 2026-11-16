"""Sound-effect library tool.

Usage:
  python scripts/sfx.py add <file> --category <tv_click|sting> --source-url <url>
                            --license <"Pixabay Content License"|"CC0"> --author <name>
      Trims edge silence, converts to 48 kHz stereo WAV, normalizes to the category's
      target, stores assets/sfx/<category>/<id>.wav and records the license entry in
      assets/sfx/sfx.json. Refuses any other license and sounds longer than the
      category's max duration (reelline/media/sfx/config.py).
  python scripts/sfx.py list [--category x]
  python scripts/sfx.py remove <id>
  python scripts/sfx.py play <id>
      Plays through ffplay if it's installed, otherwise prints the file's path.
  python scripts/sfx.py fetch --category <cat> [--query "..."] [--n 8]
      Searches Freesound (CC0 only) with the category's presets (or --query), downloads
      the HQ MP3 previews to assets_src/sfx/candidates/<cat>/ with a JSON sidecar each,
      and sends them to Telegram as numbered audio messages plus a summary.
  python scripts/sfx.py pick --category <cat> N [N ...] [N:<other category> ...]
      Adds candidates N from the last fetch through `add` (license fields from the
      sidecars), N:<other category> into that category instead, and marks the rest
      of that run rejected (candidates/rejected.json).

Every command checks the whole library first (a file in assets/sfx/ without a license
entry fails it). Credentials for fetch: FREESOUND_API_KEY, TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID from .env (reelline/config/credentials.py); none are printed. See channel docs/sfx.md.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from reelline.paths import REPO_ROOT, SCRIPTS_DIR

sys.path.insert(0, str(SCRIPTS_DIR))

from reelline.config.credentials import ConfigError, load_freesound
from reelline.media.sfx.config import CATEGORIES, FETCH, FREESOUND, LICENSES
from reelline.media.sfx.freesound import (
    FreesoundClient,
    fetch_candidates,
    pick_candidates,
    search_filter,
    send_candidates,
)
from reelline.media.sfx.library import SfxError, add_sound, by_category, load_library, remove_sound
from reelline.notify import NotifyError, get_notifier
from send_to_phone import SendError


def describe(s) -> str:
    lufs = f"{s.lufs:6.1f} LUFS" if s.lufs is not None else "     - LUFS"
    return (
        f"{s.id:<24} {s.category:<9} {s.duration_s:5.2f}s  peak {s.peak_db:5.1f} dBFS {lufs}  "
        f"{s.license:<23} {s.author}  {s.source_url}"
    )


def cmd_add(args) -> None:
    s = add_sound(args.file, args.category, args.source_url, args.license, args.author)
    print(f"Added {s.id} -> {s.path.relative_to(REPO_ROOT)}")
    print(describe(s))


def cmd_list(args) -> None:
    sounds = load_library()
    if args.category:
        sounds = by_category(sounds, args.category)
    for s in sounds:
        print(describe(s))
    counts = ", ".join(
        f"{c} {len(by_category(sounds, c))}" for c in CATEGORIES if not args.category or c == args.category
    )
    print(f"{len(sounds)} sound(s) ({counts})")


def cmd_remove(args) -> None:
    s = remove_sound(args.id)
    print(f"Removed {s.id} ({s.category}, {s.path.relative_to(REPO_ROOT)})")


def cmd_play(args) -> None:
    s = next((s for s in load_library() if s.id == args.id), None)
    if s is None:
        raise SfxError(f"No sound {args.id!r} in the library")
    if shutil.which("ffplay") is None:
        print(f"ffplay not found; the file is {s.path}")
        return
    print(f"Playing {s.id} ({s.duration_s:.2f}s): {s.path}", flush=True)
    result = subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(s.path)])
    if result.returncode != 0:
        raise SfxError(f"ffplay exited with {result.returncode}")


def cmd_fetch(args) -> None:
    settings = load_freesound()
    notifier = get_notifier()  # before any download: don't fetch what we can't send
    queries = [args.query] if args.query else list(FETCH[args.category]["queries"])
    print(
        f"Freesound {args.category}: {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}, "
        f"filter {search_filter(FETCH[args.category]['max_duration_s'])}, sort {FREESOUND['sort']}"
    )
    fetched = fetch_candidates(FreesoundClient(settings), args.category, queries, args.n)
    if fetched.dropped:
        print(f"Dropped {len(fetched.dropped)} result(s) that failed a check:")
        for what, why in fetched.dropped:
            print(f"  {what}: {why}")
    if fetched.skipped:
        print(f"Skipped {fetched.skipped} result(s) already in the library or rejected")
    if not fetched.candidates:
        raise SfxError("No new candidates; try another --query")
    saved = fetched.candidates[0][1].parent.relative_to(REPO_ROOT)
    print(f"Candidates ({len(fetched.candidates)}), saved in {saved}:")
    for number, (r, _) in enumerate(fetched.candidates, start=1):
        print(f"  {number}. {r.id:<8} {r.duration:5.2f}s  {r.username:<20} {r.name}  ({r.query!r})")
    send_candidates(notifier, args.category, fetched.candidates)
    print(f"Sent to Telegram. Pick with: python scripts/sfx.py pick --category {args.category} N [N ...]")


def parse_picks(tokens: list[str]) -> tuple[list[int], dict[int, str]]:
    """["2", "1:tv_power"] -> ([2], {1: "tv_power"})."""
    numbers, into = [], {}
    for token in tokens:
        number, _, target = token.partition(":")
        if not number.isdigit() or (_ and not target):
            raise SfxError(f"Bad pick {token!r}: use N or N:<category>")
        if target:
            into[int(number)] = target
        else:
            numbers.append(int(number))
    return numbers, into


def cmd_pick(args) -> None:
    numbers, into = parse_picks(args.picks)
    picked = pick_candidates(args.category, numbers, into=into)
    print(f"Added: {', '.join(s.id for s in picked.added) or 'none'}")
    for s in picked.added:
        print(describe(s))
    print(f"Rejected {len(picked.rejected)} candidate(s) (won't be fetched again)")
    if picked.failed:
        sys.exit("FAIL: " + "; ".join(f"candidate {n}: {reason}" for n, reason in picked.failed))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("add", help="add a sound with its license record")
    p.add_argument("file", type=Path)
    p.add_argument("--category", required=True, choices=list(CATEGORIES))
    p.add_argument("--source-url", required=True, help="the sound's page on Pixabay / Freesound")
    p.add_argument("--license", required=True, help=f"exactly one of: {', '.join(repr(k) for k in LICENSES)}")
    p.add_argument("--author", required=True)
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("list", help="list the library")
    p.add_argument("--category", choices=list(CATEGORIES), default=None)
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("remove", help="delete a sound and its entry")
    p.add_argument("id")
    p.set_defaults(func=cmd_remove)
    p = sub.add_parser("play", help="play a sound (ffplay), or print its path")
    p.add_argument("id")
    p.set_defaults(func=cmd_play)
    p = sub.add_parser("fetch", help="fetch CC0 candidates from Freesound and send them to Telegram")
    p.add_argument("--category", required=True, choices=list(FETCH))
    p.add_argument("--query", default=None, help="one search query instead of the category's presets")
    p.add_argument(
        "--n",
        type=int,
        default=FREESOUND["candidates_per_run"],
        help=f"candidates in this run (default {FREESOUND['candidates_per_run']})",
    )
    p.set_defaults(func=cmd_fetch)
    p = sub.add_parser("pick", help="add candidates from the last fetch, reject the rest")
    p.add_argument("--category", required=True, choices=list(FETCH))
    p.add_argument(
        "picks",
        nargs="+",
        metavar="N[:CATEGORY]",
        help="candidate numbers; N:<category> adds that one to another category",
    )
    p.set_defaults(func=cmd_pick)
    args = parser.parse_args()
    try:
        args.func(args)
    except (SfxError, ConfigError, SendError, NotifyError) as exc:
        sys.exit(f"FAIL: {exc}")


if __name__ == "__main__":
    main()
