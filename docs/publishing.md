# Publishing and statistics

## What publish does

After a review is approved, the publish job creates one post per *channel* (a name the pipeline stores publications under, such as `instagram`, `tiktok`, `youtube`, `facebook` or `local`). The pipeline decides what and when: the pre-flight guard, the files in blob storage, the slot, the database rows and the retries. A *publisher* (adapter) does the platform's part.

`publish.enabled` is `false` by default: turn it on when the credentials are in place. In a dry run, nothing is published.

```yaml
publish:
  enabled: true
  targets:  { instagram: buffer, tiktok: buffer, youtube: buffer, facebook: meta }   # channel -> adapter
  channels: { instagram: true,   tiktok: true,   youtube: true,   facebook: true }   # which are on
```

## Adapters

| Adapter (`publish.targets` value) | What it does |
|---|---|
| `local` | copies the render to `publish.local.dir/<channel>/` with a JSON sidecar (channel, caption, title, time, cover frame). For posting by hand or with your own tool; also what the example channel uses. |
| `buffer` | creates scheduled posts through Buffer's API for the channels configured there. |
| `meta` | posts to a Facebook Page through the Graph API; the Page id and name are checked before every run. |
| `youtube` | uploads to a YouTube channel. |
| `tiktok` | posts through TikTok's APIs. |

A publisher implements `publish(post, existing=, remember=)`, `status(channel, id)` and `delete(channel, id)` (`reelline/publish/base.py`). `existing` is the id stored by an earlier try, so a retry never double-posts; `remember(id)` stores an id the moment the platform has one. A publisher declares which URL of the file its platform fetches: `public` (blob storage's plain URL), `signed` (an expiring one) or `file` (it reads the path itself). Failures raise `PublisherError` (`QueueFull` when the platform's schedule is full), with a message that says why and never holds a secret. To add a platform, write the adapter and add a branch to `publish/registry.py`.

Before posting, the guard checks the file against `publish.max_mb`, `duration_s`, `size_px`, `video_codec` and `fps`. Platform accounts and tokens are named in the `publish.*` sections; the values live in `.env`.

## Files and storage

Platforms that fetch the video need a URL. `storage.blob.kind` picks where the files wait:

- `s3`: any S3-compatible bucket (the credentials are `publish.r2.*` fields, which name the variables for the endpoint, bucket and keys); URLs are public or presigned (expiring, at most seven days);
- `local`: a folder; nothing is uploaded. Use it with the `local` publisher.

## Credits and short links (optional)

- `publish.credits_page`: one static page per reel listing the images used and their licences, deployed to a static host; the caption can link it. Leave `project` empty to switch it off.
- `publish.shortlinks`: short redirect pages on that site; `publish.shortlinks.enabled: false` keeps the caption's own URL.
- `publish.credit_lines` and `publish.comment_lines`: the wording of credit lines and of first comments (per-platform comments are off by default).

## Statistics

The `stats` step collects each post's numbers at due snapshot times (`steps.analytics.snapshots_h`: 1 h, 24 h, 72 h, 7 days, then daily until day 14, then weekly until `weekly_until_days`). Collectors (`steps.analytics.collectors`) are `buffer`, `meta`, `youtube` and `tiktok`; each reads one or more API surfaces, and a failing surface is counted and reported (an alert after `alert_after_failures` failures) without stopping the others. The core merges them into one row per post and snapshot. Views, likes, watch time, the retention curve and followers are stored where the platform gives them; what a platform does not provide is recorded as missing, with the permission or setup that would provide it (`steps.analytics.needs`).

`reelline/pipeline/analytics` reports read-only from the stored snapshots (`/analytics` in the approval bot, in periods and per platform and slot). The `rank` step derives each reel's retention line and the week's hook examples from it. A new collector implements `StatsCollector` (`reelline/stats/base.py`) and is added to `stats/registry.py`.
