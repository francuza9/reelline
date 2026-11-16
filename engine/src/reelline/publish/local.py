"""local: "publish" into a folder (`publish.local.dir`): the channel's render copied to
<dir>/<channel>/<file name> with a JSON sidecar (<file name>.json: channel, caption, title, time,
cover frame, publication time) for whoever posts it by hand or by their own tool. It is as
published as it gets the moment it is written; the post id is the path under the folder."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from reelline.config import conf
from reelline.publish.base import MediaKind, Placed, Post, PublisherError, Status


class LocalPublisher:
    name = "local"
    media: MediaKind = "file"
    schedules = True

    def __init__(self, root: Path | None = None, channels: tuple[str, ...] | None = None, **_):
        self.root = Path(root or conf().publish.local.dir)
        self.channels = tuple(channels or conf().publish.targets)

    def _path(self, post_id: str) -> Path:
        path = (self.root / post_id).resolve()
        if self.root.resolve() not in path.parents:
            raise PublisherError(f"local: {post_id!r} leaves the folder")
        return path

    def publish(
        self,
        post: Post,
        *,
        existing: str | None = None,
        now: datetime | None = None,
        remember: Callable[[str], None] = lambda id: None,
    ) -> Placed:
        post_id = f"{post.channel}/{post.path.name}"
        dest = self._path(post_id)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(post.path, dest)
            sidecar = {
                "channel": post.channel,
                "file": dest.name,
                "caption": post.caption,
                "title": post.title,
                "first_comment": post.first_comment,
                "cover_offset_ms": post.cover_offset_ms,
                "publish_at": post.when.astimezone(UTC).isoformat(timespec="seconds") if post.when else None,
                "meta": post.meta,
            }
            if post.cover is not None:
                shutil.copyfile(post.cover, dest.with_name(f"{dest.stem}.cover{post.cover.suffix}"))
                sidecar["cover"] = f"{dest.stem}.cover{post.cover.suffix}"
            dest.with_name(dest.name + ".json").write_text(
                json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise PublisherError(f"local: {exc}") from None
        remember(post_id)
        return Placed(post_id, "published", url=dest.as_uri())

    def status(self, channel: str, post_id: str) -> Status:
        path = self._path(post_id)
        return (
            Status("published", url=path.as_uri(), detail="written")
            if path.is_file()
            else Status("gone", error="the file is gone from the folder")
        )

    def delete(self, channel: str, post_id: str) -> None:
        path = self._path(post_id)
        for p in (path, path.with_name(path.name + ".json"), *path.parent.glob(f"{path.stem}.cover.*")):
            p.unlink(missing_ok=True)

    def exists(self, channel: str, post_id: str) -> bool:
        return self._path(post_id).is_file()
