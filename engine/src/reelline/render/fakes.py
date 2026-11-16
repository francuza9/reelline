"""FakeRenderer: records the jobs and writes a stand-in file per platform (no video)."""

from __future__ import annotations

from pathlib import Path

from reelline.render.base import RenderError, RenderJob


class FakeRenderer:
    name = "fake"

    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.jobs: list[RenderJob] = []
        self.stills: list[tuple[str, Path, dict, int | None]] = []

    def render(self, job: RenderJob) -> dict[str, Path]:
        if self.fail:
            raise RenderError(self.fail)
        self.jobs.append(job)
        job.work.mkdir(parents=True, exist_ok=True)
        out = {}
        for platform in job.platforms:
            out[platform] = job.work / f"reel_{platform}.mp4"
            out[platform].write_bytes(b"fake video " + platform.encode())
        return out

    def still(self, composition: str, out: Path, props: dict, public_dir: Path, *, frame: int | None = None) -> Path:
        if self.fail:
            raise RenderError(self.fail)
        self.stills.append((composition, out, props, frame))
        out.write_bytes(b"fake still")
        return out
