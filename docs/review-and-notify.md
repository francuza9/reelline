# Review and notifications

Nothing is published without a decision. The *review channel* is where a finished reel goes for approval and where the answer comes back; the *notifier* carries alerts and reports. A review channel is also a notifier.

## Review channels

`review.channel`, one of:

| Channel | How it works |
|---|---|
| `telegram` | the reel, caption and a status block with inline buttons in a chat; taps and commands come back by long polling. Needs a bot token and chat id in `.env`. |
| `cli` | reviews are printed; decisions are typed on stdin or dropped as files into `review.cli.inbox_dir` with `reelline review tap` / `reelline review say`. |
| `webhook` | every message is POSTed as JSON to `review.webhook.url` (optionally with a bearer token); decisions are polled from `review.webhook.poll_url`. No port to open; a restart loses nothing. The message and event shapes are documented at the top of `reelline/review/webhook.py`. |
| `auto` | for unattended demos: each review is approved `review.auto.delay_s` after it is sent. The approval still goes through the same state machine and checks. |

All four share one decision logic: the review states, double taps, expiry, the regeneration limit and the jobs live in `reelline/review/machine.py` and `reelline/pipeline/approve_bot.py`. An adapter only renders messages and parses events, so a new channel is small: implement `ReviewChannel` (`reelline/review/base.py`) and add it to `review/registry.py`.

Events are channel-neutral: `Tap(data)` (a button's action data such as `r:12:ap`), `Command(name)` (`status`, `pause`, `resume`, `costs`, `analytics`, `help`, `cancel`) and `Text(text)` (a new caption while one is asked for).

## What a reviewer can do

On a review: approve; edit the caption; regenerate the script, the voice or the images; pick another story; reject with a reason (a reason listed in `review.approve.reject_story_reasons` marks the story rejected; any other reason leaves it eligible to be picked again); post now, at the next free slot, or skip the slot. Flags are shown on the message: failed fact-check claims and QA failures; when QA failed, the approval asks a yes/no confirmation first. Button labels are `review.approve.buttons`, so a channel can translate them.

From the command line, with the `cli`, `webhook` or `auto` channels:

```sh
reelline review list --channel my-channel                # pending reviews and their buttons
reelline review tap r:12:ap --channel my-channel         # press a button by its action data
reelline review say "/status" --channel my-channel       # a command, or free text (a new caption)
```

## Notifiers

`notify.channel`: `telegram` (the review chat), `slack` (an incoming webhook, `SLACK_WEBHOOK_URL`) or `log` (stdout, or `notify.log.path`). Alerts, job failures (with a *Retry* button where the channel supports buttons), statistics lines and cost reports go through it. A notifier that cannot edit or thread messages sends them as new messages.

## The approval bot

A long-running process that holds the review channel: it receives decisions, runs the jobs they mean (regenerate, publish, retry) and answers commands. In the daily run `reelline tick` supervises it (`steps.run_daily.bot_supervisor: tick`), or a service manager does (`systemd`); see [deployment](deployment.md).

## Credentials

Telegram: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (default names; the config names the variables). Only the adapters a config uses are asked for: `reelline doctor` lists exactly the missing ones.
