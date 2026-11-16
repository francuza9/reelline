"""Technical checks: the file, its encode, loudness, true peak, silences and the cover."""

from __future__ import annotations

from reelline.pipeline import qa as _qa
from reelline.pipeline.qa.base import QA, Check, Subject
from reelline.pipeline.qa.media import gag_offset


def technical_checks(s: Subject) -> list[Check]:
    if not s.mp4.is_file():
        return [Check("technical", "video", "fail", None, None, [f"{s.mp4.name} is missing"])]
    checks = []
    info = _qa.probe(s.mp4)
    lo, hi = QA["duration_s"]
    gag = gag_offset(s.reel_dir)
    # The reel's own length: a cold open before it doesn't count (reelline/media/gags.py).
    d = round(info["duration"] - gag, 2)
    checks.append(
        Check(
            "technical",
            "duration",
            "pass" if lo <= d <= hi else "fail",
            d,
            [lo, hi],
            []
            if lo <= d <= hi
            else [f"duration {d:.1f}s outside {lo:g}-{hi:g}s" + (f" (after the {gag:g}s cold open)" if gag else "")],
        )
    )
    v = info["video"]
    w, h = QA["size"]
    want = {"width": w, "height": h, "fps": QA["fps"], "codec": QA["video_codec"]}
    if v is None:
        checks.append(Check("technical", "video stream", "fail", None, want, ["no video stream"]))
    else:
        bad = [
            f"{k} {v[k]} (want {want[k]})"
            for k in ("width", "height", "fps", "codec")
            if (abs(v[k] - want[k]) > 0.01 if k == "fps" else v[k] != want[k])
        ]
        checks.append(
            Check(
                "technical",
                "video stream",
                "fail" if bad else "pass",
                {k: v[k] for k in ("codec", "width", "height", "fps")},
                want,
                [f"video: {', '.join(bad)}"] if bad else [],
            )
        )
        fmt = QA["video_format"]
        bad = [f"{k} {v.get(k)} (want {fmt[k]})" for k in fmt if v.get(k) != fmt[k]]
        checks.append(
            Check(
                "technical",
                "video format",
                "warn" if bad else "pass",
                {k: v.get(k) for k in fmt},
                fmt,
                [f"video format: {', '.join(bad)}"] if bad else [],
            )
        )
        floor, mbps = QA["min_video_mbps"], v.get("mbps")
        low = mbps is None or mbps < floor
        checks.append(
            Check(
                "technical",
                "video bitrate",
                "warn" if low else "pass",
                mbps,
                floor,
                [f"video bitrate {'unknown' if mbps is None else f'{mbps:.1f} Mbps'} (want at least {floor:g})"]
                if low
                else [],
            )
        )
    mb = s.mp4.stat().st_size / 1e6
    big = mb > QA["max_mb"]
    checks.append(
        Check(
            "technical",
            "file size",
            "fail" if big else "pass",
            round(mb, 1),
            QA["max_mb"],
            [f"{mb:.0f} MB, over the publish limit {QA['max_mb']} MB"] if big else [],
        )
    )
    checks.append(
        Check(
            "technical",
            "audio stream",
            "pass" if info["audio"] else "fail",
            info["audio"],
            True,
            [] if info["audio"] else ["no audio stream"],
        )
    )
    if info["audio"]:
        levels = _qa.audio_levels(s.mp4)
        lufs, tp = levels["lufs"], levels["tp"]
        off = abs(lufs - QA["loudness_lufs"])
        result = "fail" if off > QA["loudness_fail_lu"] else "warn" if off > QA["loudness_warn_lu"] else "pass"
        line = f"loudness {lufs:.1f} LUFS ({lufs - QA['loudness_lufs']:+.1f} LU from {QA['loudness_lufs']:g})"
        checks.append(
            Check(
                "technical",
                "loudness",
                result,
                lufs,
                {"target": QA["loudness_lufs"], "warn_lu": QA["loudness_warn_lu"], "fail_lu": QA["loudness_fail_lu"]},
                [] if result == "pass" else [line],
            )
        )
        result = "fail" if tp > QA["true_peak_fail_db"] else "warn" if tp > QA["true_peak_warn_db"] else "pass"
        checks.append(
            Check(
                "technical",
                "true peak",
                result,
                tp,
                {"warn_db": QA["true_peak_warn_db"], "fail_db": QA["true_peak_fail_db"]},
                []
                if result == "pass"
                else [
                    f"true peak {tp:.1f} dBTP (max {QA['true_peak_warn_db']:g}"
                    + ("; it clips" if result == "fail" else "")
                    + ")"
                ],
            )
        )
    if s.voice_wav is None or not s.voice_wav.is_file():
        checks.append(Check("technical", "silence", "fail", None, QA["silence_max_s"], ["voice track missing"]))
    else:
        found = [
            x for x in _qa.silences(s.voice_wav, QA["silence_db"], QA["silence_max_s"]) if x[1] > QA["silence_max_s"]
        ]
        checks.append(
            Check(
                "technical",
                "silence",
                "fail" if found else "pass",
                found,
                {"max_s": QA["silence_max_s"], "noise_db": QA["silence_db"]},
                [f"{length:.1f}s of silence at {start:.1f}s" for start, length in found],
            )
        )
    checks.append(cover_check(s))
    return checks


def cover_check(s: Subject) -> Check:
    want = list(QA["cover_size"])
    if s.cover is None or not s.cover.is_file():
        return Check("technical", "cover", "fail", None, want, ["cover.png missing"])
    from PIL import Image

    with Image.open(s.cover) as im:
        size = list(im.size)
    problems = [] if size == want else [f"cover is {size[0]}x{size[1]} (want {want[0]}x{want[1]})"]
    fresh = s.cover_fresh
    props = s.cover.parent / "cover_props.json"
    if fresh is None:
        fresh = props.is_file() and s.cover.stat().st_mtime >= props.stat().st_mtime
    if not fresh:
        problems.append("cover safe-area check not passed (cover.png older than cover_props.json)")
    return Check(
        "technical",
        "cover",
        "fail" if problems else "pass",
        {"size": size, "safe_area": bool(fresh)},
        {"size": want, "safe_area": True},
        problems,
    )
