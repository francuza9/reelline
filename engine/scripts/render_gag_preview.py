"""A short preview of the cold open (channel docs/gags.md) on a reel that's already rendered: the gag, then the
reel's first seconds, from the reel's last render (its reel_props.json, its staged remotion_public/ and
the audio in it). No TTS, no models, no image search: nothing paid. Renders run on prod (CLAUDE.md).

Usage: python scripts/render_gag_preview.py <reel dir> --gag NAME --out <file.mp4> [--seconds 8]
                                            [--work DIR]

The reel's public dir is copied with hard links (Remotion's server answers 404 for symlinks) into the
work dir (default: next to --out), and only new files are added to the copy: the gag's art under gags/
and the audio with the gag in front (reelline/media/gags.py gag_audio(), with_gag()). The props are the reel's
with that audio and the `gag` prop; Remotion renders the video's first --seconds (the gag's 1.5 s
included). The reel's own files are never written.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

from reelline.paths import SCRIPTS_DIR

sys.path.insert(0, str(SCRIPTS_DIR))

from reelline.media import gags
from reelline.media.sfx.library import SfxError, load_library
from render_reel import FPS, RenderError, face_size, remotion


def link_tree(src: Path, dest: Path) -> None:
    """`dest` a copy of `src` made of hard links (new files can be added; none is written through)."""
    for root, _, names in os.walk(src):
        out = dest / Path(root).relative_to(src)
        out.mkdir(parents=True, exist_ok=True)
        for name in names:
            os.link(Path(root) / name, out / name)


def preview(reel_dir: Path, gag: str, out: Path, seconds: float, work: Path) -> Path:
    props = json.loads((reel_dir / "reel_props.json").read_text(encoding="utf-8"))
    public_src = reel_dir / "remotion_public"
    if not (public_src / props["audioSrc"]).is_file():
        raise RenderError(f"{public_src / props['audioSrc']} is missing; render the reel first")
    cfg = gags.load()
    sounds = load_library()
    gags.check_files(cfg, [gag])
    gags.check_sounds(cfg, sounds, [gag])
    if work.exists():
        raise RenderError(f"{work} exists; give another --work")
    public = work / "public"
    link_tree(public_src, public)
    gag_wav = work / "gag.wav"
    used = gags.gag_audio(cfg, gag, sounds, gag_wav)
    audio = f"{Path(props['audioSrc']).stem}_gag_preview.wav"
    gags.with_gag(gag_wav, public_src / props["audioSrc"], public / audio)
    (public / "gags").mkdir(exist_ok=True)
    for path in gags.files(cfg, gag):
        shutil.copy2(path, public / "gags" / path.name)
    props = {**props, "audioSrc": audio, "gag": gags.props(cfg, gag, face_size(props["screen"]))}
    props_json = work / "props.json"
    props_json.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    frames = math.ceil(seconds * FPS)
    out.parent.mkdir(parents=True, exist_ok=True)
    remotion(["render", "Reel", str(out), f"--frames=0-{frames - 1}"], props_json, public)
    print(
        f"Preview: {out} ({frames} frames: the {gag} cold open {cfg.duration_s:g}s, caught at {cfg.caught_s:g}s, "
        f"then {seconds - cfg.duration_s:g}s of {reel_dir.name}); sound {used['action']} {used['action_db']:+.1f} dB, "
        f"{used['scratch']} {used['scratch_db']:+.1f} dB"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reel_dir", type=Path, help="out/reels/<name> of a rendered reel")
    parser.add_argument("--gag", required=True, choices=sorted(gags.load().gags))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=8.0, help="the video's first seconds (default 8)")
    parser.add_argument("--work", type=Path, default=None, help="scratch dir (default: <out>.work next to --out)")
    args = parser.parse_args()
    if args.seconds <= gags.load().duration_s:
        sys.exit(f"FAIL: --seconds must be longer than the cold open ({gags.load().duration_s:g}s)")
    try:
        preview(
            args.reel_dir, args.gag, args.out, args.seconds, args.work or args.out.with_name(args.out.stem + ".work")
        )
    except (RenderError, gags.GagError, SfxError, OSError, KeyError) as exc:
        sys.exit(f"FAIL: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
