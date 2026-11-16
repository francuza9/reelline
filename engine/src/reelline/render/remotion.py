"""The Remotion adapter of the Renderer protocol (engine/remotion): the only place that runs `npx remotion`.

The CLI runner (remotion(), remotion_bundle()), its failure text, and the per-platform versions
(render_versions(): one bundle, the head and rest once, the share arrow's stretch per platform, joined by
stream copy; reelline/media/share.py) come from scripts/render_reel.py unchanged. RemotionRenderer wraps them
behind Renderer: render(job) -> {platform: mp4}, still(). The composition's own props (captions, cues, motion,
anchors) are built by the render script; the rig's drawings are staged into the public dir by HostRig.stage().
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

from reelline.config import conf
from reelline.media.share import PLATFORMS as SHARE_PLATFORMS
from reelline.media.share import (
    ShareError,
    check_join,
    clear_versions,
    encode_audio,
    join,
    link,
    link_versions,
    platform_file,
    reel_frames,
    segments,
)
from reelline.paths import REMOTION_DIR, REPO_ROOT
from reelline.render.base import RenderError, RenderJob

FPS = 30
# A failed Remotion run's RenderError carries the last REMOTION_ERROR_LINES non-blank
# lines of its stderr (its progress goes to stdout), each cut to REMOTION_LINE_CHARS,
# and at most their last REMOTION_ERROR_CHARS characters (the channel config
# steps.produce.error_chars and review.approve.job_error_chars hold two attempts' worth).
REMOTION_ERROR_LINES = 20
REMOTION_LINE_CHARS = 200
REMOTION_ERROR_CHARS = 1500
# Remotion's temp files (TMPDIR): its bundle and, when it doesn't pipe the frames to ffmpeg,
# every frame as a file (remotion/encode.json imageFormat; PNG ~1.8 MB, ~3 GB for a 58 s reel; JPEG 95 far less).
# It pipes only with ~4 GB free (Remotion's prestitcher estimate), which the server under a
# render's Python doesn't have; on its tmpfs /tmp the frames would count as the render slice's
# memory (unreclaimable without swap) and stall it at MemoryHigh. On disk, one dir per run.
REMOTION_TMP = REPO_ROOT / "out" / "remotion_tmp"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def remotion(args: list[str], props_json: Path | None, public_dir: Path | None, bundle: Path | None = None) -> None:
    """Run the Remotion CLI (args: verb, composition, ...). Its stderr passes through to
    ours as it comes (the job log keeps all of it); a failure raises RenderError naming
    the composition, with the end of that stderr (remotion_failure()). Its TMPDIR is a dir
    of its own under REMOTION_TMP (on disk), removed afterwards. `bundle`: render from that
    Remotion bundle (remotion_bundle(); its public dir is in it) instead of the source.
    render.remotion_concurrency, when set, goes to remotion.config.ts as REELLINE_REMOTION_CONCURRENCY;
    the channel's folder as REELLINE_CHANNEL_DIR (the channel's graphics and mascot, remotion.config.ts)."""
    concurrency = conf().render.remotion_concurrency
    env = {
        **os.environ,
        # remotion.config.ts resolves @channel/... (graphics, anchors, the mascot) here first.
        "REELLINE_CHANNEL_DIR": str(conf().channel.dir),
        **({"REELLINE_REMOTION_CONCURRENCY": str(concurrency)} if concurrency else {}),
    }
    cmd = [
        "npx",
        "remotion",
        args[0],
        *([str(bundle)] if bundle else []),
        *args[1:],
        *([f"--props={props_json}"] if props_json else []),
        *([f"--public-dir={public_dir}"] if public_dir and not bundle else []),
    ]
    print("Running:", " ".join(cmd), flush=True)
    tail: deque[str] = deque(maxlen=REMOTION_ERROR_LINES)
    REMOTION_TMP.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(dir=REMOTION_TMP) as tmp,
        subprocess.Popen(
            cmd,
            cwd=REMOTION_DIR,
            env={**env, "TMPDIR": tmp},
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as proc,
    ):
        for line in proc.stderr:
            sys.stderr.write(line)
            sys.stderr.flush()
            line = ANSI_ESCAPE.sub("", line).rstrip()
            if line.strip():
                tail.append(line if len(line) <= REMOTION_LINE_CHARS else line[:REMOTION_LINE_CHARS] + " [...]")
    if proc.returncode != 0:
        raise RenderError(remotion_failure(args, proc.returncode, list(tail)))


def remotion_bundle(out_dir: Path, public_dir: Path) -> None:
    """The Remotion bundle of the composition and `public_dir` in `out_dir`, for several
    renders without bundling each time (render_versions())."""
    remotion(["bundle", "src/index.ts", f"--out-dir={out_dir}"], None, public_dir)


def remotion_failure(args: list[str], code: int, stderr_tail: list[str]) -> str:
    """'Remotion Cover still exited with 1; stderr:' and the tail, one line each."""
    if code < 0:
        try:
            how = f"was killed by {signal.Signals(-code).name}"
        except ValueError:
            how = f"was killed by signal {-code}"
    else:
        how = f"exited with {code}"
    head = f"Remotion {args[1]} {args[0]} {how}"
    if not stderr_tail:
        return f"{head} (nothing on stderr)"
    text = "\n".join(stderr_tail)
    if len(text) > REMOTION_ERROR_CHARS:
        text = "[...]" + text[-REMOTION_ERROR_CHARS:]
    return f"{head}; stderr:\n{text}"


VERSIONS_FILE = "versions.json"


def render_segments(
    tmp: Path,
    work: Path,
    props: dict,
    props_json: Path,
    public_dir: Path,
    audio: Path,
    share: dict,
    seg,
    record: dict,
    timed,
) -> None:
    """render_versions()'s segments in `tmp`: the bundle, the head and the rest from the reel's
    props, the mix's AAC, then per platform the arrow's stretch from its own props, joined and
    checked into <work>/reel_<platform>.mp4 (record["checks"], ["files"])."""
    bundle = tmp / "bundle"
    timed("bundle", lambda: remotion_bundle(bundle, public_dir))
    head, rest = tmp / "head.mp4", tmp / "rest.mp4"
    timed(
        "head",
        lambda: remotion(
            ["render", "Reel", str(head), f"--frames={seg.head}", "--muted"], props_json, None, bundle=bundle
        ),
    )
    if seg.rest is not None:
        timed(
            "rest",
            lambda: remotion(
                ["render", "Reel", str(rest), f"--frames={seg.rest}", "--muted"], props_json, None, bundle=bundle
            ),
        )
    aac = tmp / "audio.m4a"
    timed("audio", lambda: encode_audio(audio, aac))
    record["checks"] = {}
    for platform in SHARE_PLATFORMS:
        own = tmp / f"props_{platform}.json"
        own.write_text(
            json.dumps({**props, "share": {**share, "platform": platform}}, ensure_ascii=False), encoding="utf-8"
        )
        window = tmp / f"arrow_{platform}.mp4"
        timed(
            f"arrow_{platform}",
            lambda window=window, own=own: remotion(
                ["render", "Reel", str(window), f"--frames={seg.window}", "--muted"], own, None, bundle=bundle
            ),
        )
        parts = [head, window] + ([rest] if seg.rest is not None else [])
        out = platform_file(work, platform)
        t0 = time.monotonic()
        record["checks"][platform] = check_join(join(parts, aac, out, tmp), parts, aac)
        record["render_s"][f"join_{platform}"] = round(time.monotonic() - t0, 1)
        record["files"][platform] = out.name


def render_versions(
    work: Path,
    props: dict,
    props_json: Path,
    public_dir: Path,
    audio: Path,
    reel_end: float,
    share_cfg,
    gag_frames: int = 0,
    fps: int = FPS,
) -> dict:
    """reel.mp4 and each platform's version, <work>/reel_<platform>.mp4 (reelline/media/share.py).

    Without an arrow (props["share"] None: no share line, or share.json off) the reel is
    rendered once, as before, and every platform's file is that file. With one: one
    Remotion bundle; the frames before and after the arrow rendered once without it, the
    arrow's stretch once per platform (props with share.platform set), all muted; each
    platform's segments joined by stream copy over one AAC encode of `audio` (the mix),
    and each join checked (reelline/media/share.py check_join()). reel.mp4 is the review platform's
    file. gag_frames: the cold open's frames before the reel (reelline/media/gags.py), which move the
    arrow's stretch in the MP4 (its times in the props are the reel's). The record (segments,
    render seconds, checks) goes to versions.json and is returned."""
    mp4 = work / "reel.mp4"
    clear_versions(work)
    share = props.get("share")
    record: dict = {
        "arrow": share is not None,
        "review": share_cfg.review_platform,
        "share": share,
        "render_s": {},
        "files": {},
    }
    if share is None:
        t0 = time.monotonic()
        remotion(["render", "Reel", str(mp4)], props_json, public_dir)
        record["render_s"]["reel"] = round(time.monotonic() - t0, 1)
        record["files"] = {p: path.name for p, path in link_versions(work, mp4).items()}
        (work / VERSIONS_FILE).write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return record
    try:
        seg = segments(share, gag_frames + reel_frames(reel_end, fps), fps, gag_frames)
    except ShareError as exc:
        raise RenderError(str(exc)) from None
    record["segments"] = {"head": seg.head, "arrow": seg.window, "rest": seg.rest}
    print(
        f"Versions: the arrow's stretch is {seg.describe(fps)}; rendering the rest once, that per platform", flush=True
    )
    timer = record["render_s"]

    def timed(key: str, fn) -> None:
        t0 = time.monotonic()
        fn()
        timer[key] = round(time.monotonic() - t0, 1)

    REMOTION_TMP.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=REMOTION_TMP) as tmp_name:
            render_segments(Path(tmp_name), work, props, props_json, public_dir, audio, share, seg, record, timed)
    except ShareError as exc:
        clear_versions(work)
        raise RenderError(f"versions: {exc}") from None
    except BaseException:
        clear_versions(work)  # no half set of versions to publish
        raise
    link(platform_file(work, share_cfg.review_platform), mp4)
    arrows = sum(v for k, v in timer.items() if k.startswith("arrow_"))
    joins = sum(v for k, v in timer.items() if k.startswith("join_"))
    record["arrow_total_s"] = round(arrows, 1)
    print(
        f"Versions: head {timer['head']:.0f}s"
        + (f", rest {timer['rest']:.0f}s" if "rest" in timer else "")
        + f", the arrow's stretch {arrows:.0f}s for {len(SHARE_PLATFORMS)} platforms, bundle {timer['bundle']:.0f}s, "
        f"audio and joins {timer['audio'] + joins:.0f}s; {mp4.name} = {share_cfg.review_platform}'s version"
    )
    (work / VERSIONS_FILE).write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def run_cli(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    """`npx remotion <args>` in remotion/, for the preview scripts: output passes through, or is captured."""
    cmd = ["npx", "remotion", *args]
    if not capture:
        print("Running:", " ".join(cmd), flush=True)
        return subprocess.run(cmd, cwd=REMOTION_DIR)
    return subprocess.run(cmd, cwd=REMOTION_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")


class RemotionRenderer:
    """Renderer over the Remotion CLI. The reel's frames are the composition's (props.json); `share` in the
    props decides between one render and the per-platform versions."""

    name = "remotion"

    def __init__(self, fps: int = FPS):
        self.fps = fps

    def render(self, job: RenderJob) -> dict[str, Path]:
        public = job.public_dir or job.work / "remotion_public"
        props_json = job.work / "reel_props.json"
        props_json.write_text(json.dumps(job.props, ensure_ascii=False), encoding="utf-8")
        from reelline.media.share import load as load_share

        record = render_versions(
            job.work,
            job.props,
            props_json,
            public,
            job.audio,
            job.props["reelEnd"],
            load_share(),
            job.props.get("gagFrames", 0),
            self.fps,
        )
        return {p: platform_file(job.work, p) for p in job.platforms if p in record["files"]}

    def still(self, composition: str, out: Path, props: dict, public_dir: Path, *, frame: int | None = None) -> Path:
        props_json = out.with_suffix(".props.json")
        props_json.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
        remotion(
            ["still", composition, str(out), *([f"--frame={frame}"] if frame is not None else [])],
            props_json,
            public_dir,
        )
        return out
