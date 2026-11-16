"""Message text shared by every channel: the header between two rules, the "Reel #5 · " prefix
(review.telegram.header: the review chat's settings, the same strings on any channel)."""

from __future__ import annotations

from reelline.config import conf


def framed(line: str) -> str:
    """A header message: the line between two rules (before a review or a pick menu)."""
    rule = conf().review.telegram.header["rule"]
    return f"{rule}\n{line}\n{rule}"


def about_reel(reel_id: int, text: str) -> str:
    """A message about a reel, prefixed with which one ("Reel #5 · ...")."""
    return conf().review.telegram.header["reel_prefix"].format(reel_id=reel_id) + text
