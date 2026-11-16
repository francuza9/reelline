"""Bot supervision and the tick's alerts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline.run_daily.advance import (
    review_out,
)
from reelline.pipeline.run_daily.base import hm
from reelline.pipeline.run_daily.chains import get_chain, last_job, slot_reel
from reelline.pipeline.run_daily.effects import Effects, once
from reelline.pipeline.run_daily.slots import (
    candidate_slots,
    chain_start,
    slot_dt,
)


def supervise_bot(conn: sqlite3.Connection, fx: Effects, now: datetime) -> None:
    beat = db.get_state(conn, "heartbeat")
    if beat and now - datetime.fromisoformat(beat) < timedelta(minutes=conf().steps.run_daily.bot_heartbeat_stale_min):
        return
    key = beat or "never"
    if fx.supervisor() == "systemd":
        if once(conn, "bot_stale", key, now):
            fx.say(
                f"⚠️ The approval bot hasn't polled since {beat or 'ever'}; systemd restarts it "
                f"(journalctl -u {conf().channel.name}-bot)."
            )
        fx.did("bot heartbeat stale (systemd supervises it): alerted")
        return
    if fx.bot_running():
        if once(conn, "bot_stale", key, now):
            fx.say(f"⚠️ The approval bot is running but hasn't polled since {beat or 'ever'} (hung?). Restart it.")
        fx.did("bot heartbeat stale, a bot process exists: alerted")
        return
    child = fx.start_bot()
    fx.did(f"bot restarted (pid {getattr(child, 'pid', None)}), last heartbeat {beat}")
    if once(conn, "bot_stale", key, now):
        fx.say(f"🔄 The approval bot wasn't running (last heartbeat {beat or 'never'}): restarted.")


def thinking_alerts(conn: sqlite3.Connection, fx: Effects, now: datetime) -> int:
    """One alert per Claude call (llm_calls, the last steps.run_daily.thinking_alert_days
    days, trials included) whose thinking took more than models.writer.thinking_alert_share of
    models.writer.max_tokens (every Claude role has the same): the next ceiling, before it breaks a
    live run. How many were said."""

    ceiling = conf().models.writer.max_tokens
    share = conf().models.writer.thinking_alert_share
    limit = share * ceiling
    since = db.now_iso(now - timedelta(days=conf().steps.run_daily.thinking_alert_days))
    said = 0
    for call in conn.execute(
        """SELECT * FROM llm_calls WHERE model LIKE 'claude-%' AND thinking_tokens > ?
                                AND called_at >= ? ORDER BY id""",
        (limit, since),
    ).fetchall():
        if not once(conn, "thinking_high", str(call["id"]), now):
            continue
        where = (
            "a manual run" if call["manual"] else (f"script #{call['script_id']}" if call["script_id"] else "no script")
        )
        fx.say(
            f"⚠️ Claude thinking near the ceiling: call #{call['id']} ({call['purpose']}, {where}) thought "
            f"{call['thinking_tokens']:,} tokens of max_tokens {ceiling:,} "
            f"({call['thinking_tokens'] / ceiling:.0%}; the alert is above {share:.0%}). "
            f"At max_tokens the call fails: raise max_tokens of the Claude roles (models.writer, ...: the "
            f"channel's config.yaml) before then."
        )
        said += 1
    return said


def late_alerts(conn: sqlite3.Connection, fx: Effects, now: datetime) -> None:
    """One alert per slot that has no review this long before it (a slot a reel holds,
    slot_reel(), has what it needs, even when that reel went out on another day)."""
    from reelline.pipeline import approve_bot as ab

    for d, t in candidate_slots(now):
        sdt = slot_dt(d, t)
        if not sdt - timedelta(hours=conf().steps.run_daily.alert_no_review_before_h) <= now < sdt:
            continue
        chain = get_chain(conn, d, t)
        if (chain is not None and chain["status"] == "skipped") or slot_reel(conn, d, t) is not None:
            continue
        pick = db.slot_pick(conn, d, t)
        if pick is not None and (pick["status"] in ("approved", "posted", "skipped") or review_out(conn, pick)):
            continue
        if once(conn, "late", f"{d} {t}", now):
            where = "no chain" if chain is None else f"chain {chain['status']}, step {chain['step'] or '-'}"
            fx.say(
                f"⏰ {d} {t}: no review yet, {int((sdt - now).total_seconds() // 60)} min before the slot "
                f"({where}; {ab.slot_state(conn, d, t)})."
            )


def waited_alert(conn: sqlite3.Connection, slot_date: str, slot_time: str, busy: tuple[str, str], fx: Effects) -> None:
    """One alert when the slot still waits steps.run_daily.wait_alert_after_min after its start."""
    from reelline.pipeline import approve_bot as ab

    now, start = fx.now(), chain_start(slot_date, slot_time)
    chain = get_chain(conn, slot_date, slot_time)
    pick = db.slot_pick(conn, slot_date, slot_time)
    if (
        (chain is not None and chain["status"] in ("done", "skipped", "failed"))
        or (pick is not None and pick["status"] not in ("picked", "scripted", "render_failed"))
        or last_job(conn, slot_date, slot_time) is not None
    ):
        return  # nothing left to start, its reel is made, or it started
    if now < start + timedelta(minutes=conf().steps.run_daily.wait_alert_after_min) or not once(
        conn, "waiting", f"{slot_date} {slot_time}", now
    ):
        return
    job = ab.active_job(conn, *busy)
    since = job["step_at"] or job["started_at"] or job["created_at"]
    fx.say(
        f"⏳ {slot_date} {slot_time}: not started, {int((now - start).total_seconds() // 60)} min after its start "
        f"({hm(start)}): waiting for {busy[0]} {busy[1]}'s job #{job['id']} ({ab.JOB_LABELS[job['kind']]}, step "
        f"{job['step'] or '-'} since {hm(datetime.fromisoformat(since))})."
    )
