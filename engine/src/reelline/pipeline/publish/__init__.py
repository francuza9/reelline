"""Publishing an approved reel on Instagram, TikTok and YouTube (Buffer) and the
Facebook Page (Graph API). publish in the channel config has every flag, limit,
timeout and lifetime; publish.enabled off = the dry-run message, nothing uploaded.

Usage:
  python -m reelline.pipeline.publish test <reel_id>
      The live test, nothing public: guard, R2 upload, a fetch check of the presigned
      URL with curl, the four posts scheduled publish.live_test_ahead_h ahead, each
      read back from its API (payload dump in storage.publish_test_dir), then all four
      deleted, the R2 objects removed, and the queues checked empty. Ignores
      publish.enabled (it never leaves anything scheduled). Exit 1 on any failure.
  python -m reelline.pipeline.publish status <reel_id>
      The reel's publications and its last API calls.

The flow (reelline/pipeline/approve_bot.py drives it):
  1. Approve -> enqueue(): the guard (the file exists, at most publish.max_mb, and
     its duration, size, codec and frame rate within PUBLISH; the cover and the
     caption/title are there), then one publication per enabled channel, state
     waiting. Nothing leaves the machine yet: an Unschedule now is database only.
  2. publish.buffer_lead_h before the slot (at once when approved later, or for
     Post now) the bot's tick starts a "publish" worker job -> publish_reel(): the
     guard again (it includes the credits rule: a reel that shows images has the
     credits line on caption line 4 at the latest, one that shows none has no line; and
     every shortlink in the caption is one of the reel's); the site deployed once when
     the reel shows images or has shortlinks, then its credits page and each of its
     shortlinks checked live, each link redirecting exactly to its target
     (reelline/pipeline/credits.py publish_site(); a failure stops posting); each waiting
     channel's own file to R2 (its platform version,
     reel_<channel>.mp4 with its share arrow, reelline/media/share.py; reel.mp4 for a reel from
     before the versions) under the reel's unguessable prefix (publish.r2_key; a file
     two channels share goes up once); a presigned GET URL living
     until the slot + publish.url_after_slot_h (at most publish.url_max_days);
     then each waiting channel, in order, with its own file, its external id stored before the next:
       Buffer (IG, TikTok, YT): createPost scheduled for the slot (shareNow when the
         slot is within publish.now_within_min): the assembled caption; YouTube its
         title, category, privacy, kids and notify settings; the AI flag on all three;
         IG a Reel shared to the feed; IG and TikTok the cover frame
         publish.thumbnail_offset_ms into the reel itself (past its cold open, reels.gag_s:
         cover_offset_ms(), never a gag frame); IG Buffer's own first comment only with
         publish.instagram_first_comment (off). The queue is checked against the plan's
         scheduled-posts limit first (full = that channel fails with the reason).
       Facebook: the Page checked (id and name), then start / upload by URL /
         cover.png as the thumbnail / finish SCHEDULED for the slot (PUBLISHED now),
         the caption as description, is_ai_generated.
     A channel that fails is reported with a Retry button for that channel only; with
     2+ failed, a 🔁 Retry all above them. A shared step before the channels (the
     guard, the credits page, the R2 upload) that fails is reported once
     ("❌ credits page: ..."), with one 🔁 Retry all: one publish job for every failed and
     waiting channel of the reel, the shared steps once, scheduled and published ones
     left as they are. A Retry while the reel's publish job is queued or running says
     "already running" and changes nothing.
  3. From the slot the tick polls each scheduled post every publish.poll_interval_min
     until published (its URL recorded) or failed (the platform's error, or still not
     live publish.poll_timeout_h after the slot). Once a post is live, the reel's links
     go under it as a comment where its channel's flag is on (reelline/pipeline/comments.py,
     publish.comments; a pending one is tried again every round; a failure is an alert
     with its own Retry, never a failed publication).
  4. All enabled channels published: reel published, pick posted, the four links in
     the chat, the reel's R2 objects (every key in reels.r2_keys) deleted (the bucket's
     lifecycle rule is the backstop for the rest). Some failed: reel partially_published, Retry buttons for those.
     A reel that goes out before its slot (Post now, publish live) keeps it: its pick
     stays on the slot, posted, and the slot never gets another chain
     (reelline/pipeline/run_daily.py slot_reel()).

Idempotent: a publication with an external id is checked on its platform before
anything is created (scheduled / sent stays; a failed or draft one is deleted and
made again); without one, Buffer is searched for a post with the same text created in
the last day (a crash between create and store) and Facebook's start phase stores its
video id before the upload. A double tap or a retry never makes a second post.

Unschedule (until publish.unschedule_until_min before the slot): every created
post deleted (Buffer deletePost, Facebook DELETE), the R2 objects removed, the
publications unscheduled, and the reel back to rendered with its review buttons.

Every API call is in publish_calls (reelline/pipeline/db.py): action, endpoint, a redacted
request (no tokens, no URL signatures), status, external id, error.
"""

from __future__ import annotations

from reelline.core.errors import PublishError
from reelline.pipeline.publish.base import (
    BUFFER_CHANNELS,
    CHANNEL_ERRORS,
    CHANNELS,
    TERMINAL,
    Outcome,
    enabled_channels,
    label,
    local_hm,
    local_when,
    utcnow,
)
from reelline.pipeline.publish.cli import main, status_text
from reelline.pipeline.publish.clients import CallLog, Clients, platform_client, real_clients
from reelline.pipeline.publish.flow import Urls, fail_shared, place, publish_reel, upload_and_presign
from reelline.pipeline.publish.golive import go_live, repost
from reelline.pipeline.publish.guard import (
    channel_files,
    cover_offset_ms,
    file_problems,
    guard,
    reel_row,
    shown_images,
    texts,
)
from reelline.pipeline.publish.lifecycle import (
    can_unschedule,
    cleanup_r2,
    mark_deleted,
    needs_network,
    outcome_text,
    retry,
    retry_all,
    retry_markup,
    revert,
    unschedule,
    went_out_at,
)
from reelline.pipeline.publish.livetest import curl_check, live_test, redact, url_checks
from reelline.pipeline.publish.poll import check, finalize, poll
from reelline.pipeline.publish.queue import dry_run_text, due, enqueue, get_pub, publications, set_pub, set_reel
from reelline.pipeline.publish.verify import expected_posts, http_text, norm, page_facts, verify

__all__ = [
    "BUFFER_CHANNELS",
    "CHANNELS",
    "CHANNEL_ERRORS",
    "TERMINAL",
    "CallLog",
    "Clients",
    "Outcome",
    "PublishError",
    "Urls",
    "can_unschedule",
    "channel_files",
    "check",
    "cleanup_r2",
    "cover_offset_ms",
    "curl_check",
    "dry_run_text",
    "due",
    "enabled_channels",
    "enqueue",
    "expected_posts",
    "fail_shared",
    "file_problems",
    "finalize",
    "get_pub",
    "go_live",
    "guard",
    "http_text",
    "label",
    "live_test",
    "local_hm",
    "local_when",
    "main",
    "mark_deleted",
    "needs_network",
    "norm",
    "outcome_text",
    "page_facts",
    "place",
    "platform_client",
    "poll",
    "publications",
    "publish_reel",
    "real_clients",
    "redact",
    "reel_row",
    "repost",
    "retry",
    "retry_all",
    "retry_markup",
    "revert",
    "set_pub",
    "set_reel",
    "shown_images",
    "status_text",
    "texts",
    "unschedule",
    "upload_and_presign",
    "url_checks",
    "utcnow",
    "verify",
    "went_out_at",
]
