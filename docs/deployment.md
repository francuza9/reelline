# Deployment

Two things run: the **daily tick** (`reelline tick`, a short-lived process every few minutes that advances whatever is due) and, if you use an interactive review channel, the **approval bot** (a long-running listener that handles decisions and publishes). The tick starts and supervises the bot by default (`steps.run_daily.bot_supervisor: tick`); set it to `systemd` if a service manager does that. The tick is idempotent: a chain holds a lock in the database (pid and heartbeat), a second tick skips a chain being advanced, and a chain whose process died is released and reported.

State lives in the channel folder (database under `data/`, files under `content/`) and `out/` (renders, logs, scratch). Back these up; the engine holds none.

Always start with `reelline doctor --channel <dir>`: it validates the config, the chain and the paths and lists the credentials that are missing (names only) for the adapters you actually use.

## Docker / compose

The image holds the engine, Node with Remotion's packages and browser, ffmpeg, fonts and Rhubarb. A channel is *not* in it: mount a folder (config, prompts, sources, assets, `.env`, and its state) and point `REELLINE_CHANNEL` at it.

```sh
CHANNEL_DIR=./my-channel docker compose up --build         # a tick every TICK_INTERVAL_S (default 300) seconds
docker compose run --rm reelline doctor                     # validate first
docker run --rm -v ./my-channel:/channel reelline doctor    # without compose
```

`compose.yaml` mounts `${CHANNEL_DIR}` at `/channel` and a volume for `out/`, sets `TZ` and restarts the service unless stopped. It runs the tick in a loop; for the bot, a second service running `reelline` with `python -m reelline.pipeline.approve_bot` as its command can be added when the review channel needs one. The CI builds the image on every push and runs `doctor` on the example inside it.

## systemd timer

A timer for the tick and a service for the bot (adapt users and paths):

```ini
# /etc/systemd/system/reelline-tick.service
[Service]
Type=oneshot
User=reelline
WorkingDirectory=/srv/reelline
Environment=REELLINE_CHANNEL=/srv/reelline/channels/my-channel
ExecStart=/srv/reelline/.venv/bin/reelline tick
TimeoutStartSec=5min

# /etc/systemd/system/reelline-tick.timer
[Timer]
OnCalendar=*:0/10
Persistent=true
[Install]
WantedBy=timers.target

# /etc/systemd/system/reelline-bot.service   (set steps.run_daily.bot_supervisor=systemd)
[Service]
User=reelline
WorkingDirectory=/srv/reelline
Environment=REELLINE_CHANNEL=/srv/reelline/channels/my-channel
ExecStart=/srv/reelline/.venv/bin/python -m reelline.pipeline.approve_bot
Restart=always
[Install]
WantedBy=multi-user.target
```

The tick's interval is `steps.run_daily.tick_every_min` (default 10); the timer should match it.

## cron

```cron
*/10 * * * *  cd /srv/reelline && REELLINE_CHANNEL=/srv/reelline/channels/my-channel .venv/bin/reelline tick >> logs/tick.log 2>&1
```

With cron the tick supervises the bot itself (the default). The tick prunes its own logs after `steps.run_daily.log_keep_days`.

## Settings per machine

Any setting can be set from the process environment or the `.env` as `REELLINE__SECTION__KEY=value` (for example `REELLINE__STEPS__RUN_DAILY__BOT_SUPERVISOR=systemd`), so the machine's choices stay out of the channel's `config.yaml`. The credentials come only from the channel's `.env` (else the repo root's), never from the shell environment.

## Resource limits

Rendering is the heavy part (headless Chrome plus ffmpeg), and forced alignment loads a speech model in memory (CPU torch). On a shared or small machine:

- lower `render.remotion_concurrency` (parallel frames; unset means half the CPUs);
- cap the services with the service manager (`MemoryHigh`/`MemoryMax`, `CPUQuota`, a lower `CPUWeight`/`Nice` in a systemd slice; Docker `--memory`/`--cpus`);
- the jobs have time limits of their own (`steps.run_daily.step_max_min`, per job kind; `step_max_default_min`) and a resume limit (`max_resumes`); a stuck job is reported and released instead of holding the lock;
- `steps.produce.render_attempts` bounds render retries.

Remember that a render may need free disk in `out/` for scratch frames.

## Updating

Pull, reinstall dependencies if the requirements changed, and restart the bot. Do it when no job is running: a deploy during a render kills it (the chain resumes from its last stored pass on the next tick, but the render restarts).
