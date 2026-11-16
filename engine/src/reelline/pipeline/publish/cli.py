"""Command line: test and status."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from reelline.config import conf
from reelline.pipeline import db
from reelline.pipeline import publish as _pkg
from reelline.pipeline.publish.base import CHANNELS, label
from reelline.pipeline.publish.clients import real_clients
from reelline.pipeline.publish.golive import go_live, repost
from reelline.pipeline.publish.guard import reel_row
from reelline.pipeline.publish.lifecycle import mark_deleted
from reelline.pipeline.publish.livetest import live_test, redact
from reelline.pipeline.publish.queue import publications
from reelline.pipeline.publish.verify import verify


def status_text(conn: sqlite3.Connection, reel_id: int) -> str:
    out = [f"Reel #{reel_id}: {reel_row(conn, reel_id)['status']}"]
    for p in publications(conn, reel_id):
        out.append(
            f"  {label(p['channel']):<9} {p['state']:<11} {p['external_id'] or '-'} due {p['due_at']} "
            f"{p['url'] or ''} {p['error'] or ''}"
        )
    for c in conn.execute(
        "SELECT * FROM publish_calls WHERE reel_id = ? ORDER BY id DESC LIMIT 20", (reel_id,)
    ).fetchall()[::-1]:
        out.append(
            f"  {c['at']} {c['action']} {c['method']} {c['endpoint']} -> {c['status']} "
            f"{c['external_id'] or ''} {c['error'] or ''}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m reelline.pipeline.publish", description=(_pkg.__doc__ or "").splitlines()[0]
    )
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("test", help="the live test: schedule far ahead, read back, delete (nothing public)")
    p.add_argument("reel_id", type=int)
    p.add_argument("--channels", nargs="+", choices=CHANNELS, help="only these (default: every enabled one)")
    p = sub.add_parser("live", help="PUBLISH NOW, publicly, on every enabled channel; verify; delete the FB reel")
    p.add_argument("reel_id", type=int)
    p.add_argument("--public", action="store_true", required=True, help="required: this posts publicly")
    p = sub.add_parser("verify", help="check a reel's live posts against what was sent (no posting)")
    p.add_argument("reel_id", type=int)
    p.add_argument("--channels", nargs="+", choices=CHANNELS)
    p = sub.add_parser("repost", help="POST NOW, publicly, a reel that went out already, on these channels")
    p.add_argument("reel_id", type=int)
    p.add_argument("--channels", nargs="+", choices=CHANNELS, required=True)
    p.add_argument("--public", action="store_true", required=True, help="required: this posts publicly")
    p = sub.add_parser("deleted", help="record posts taken down (by hand or elsewhere); no API call")
    p.add_argument("reel_id", type=int)
    p.add_argument("channels", nargs="+", choices=CHANNELS)
    p.add_argument("--how", default="by hand")
    p = sub.add_parser("status", help="a reel's publications and recent API calls")
    p.add_argument("reel_id", type=int)
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    if args.command == "status":
        print(status_text(conn, args.reel_id))
        return 0
    if args.command == "repost":
        from reelline.notify import get_notifier
        from reelline.pipeline.sources import load_sources

        notifier = get_notifier()
        result = repost(
            conn,
            args.reel_id,
            args.channels,
            real_clients(conn, args.reel_id, prefix="repost:"),
            source_names={s.id: s.name for s in load_sources(conf().sources)},
            send=lambda text, markup: notifier.send(text, markup),
        )
        for ch, (state, value) in result.items():
            print(f"{label(ch)}: {state} {value}")
        return 0 if all(st == "published" for st, _ in result.values()) else 1
    if args.command == "deleted":
        for ch in args.channels:
            status = mark_deleted(conn, args.reel_id, ch, how=args.how)
            print(f"Reel #{args.reel_id}: {label(ch)} deleted ({args.how}); reel {status}")
        return 0
    if args.command == "verify":
        result = verify(conn, args.reel_id, real_clients(conn, args.reel_id, prefix="verify:"), channels=args.channels)
        print(json.dumps(redact(result), ensure_ascii=False, indent=1, default=str))
        return 0 if not any(v["diffs"] for v in result.values()) else 1
    if args.command == "live":
        from reelline.notify import get_notifier

        notifier = get_notifier()
        report = go_live(
            conn,
            args.reel_id,
            real_clients(conn, args.reel_id, prefix="live:"),
            send=lambda text, markup: notifier.send(text, markup),
        )
        print(
            "\n"
            + "\n".join(
                f"{'✅' if x['ok'] else '❌'} {x['step']}" + (f": {x['detail']}" if not x["ok"] else "")
                for x in report["steps"]
            )
        )
        for ch, v in (report.get("verify") or {}).items():
            print(f"{label(ch)}: {v.get('url')}")
        print("\nLIVE RUN " + ("PASSED" if report["ok"] else "FAILED"))
        return 0 if report["ok"] else 1
    report = live_test(conn, args.reel_id, real_clients(conn, args.reel_id, prefix="test:"), channels=args.channels)
    print(
        "\n"
        + "\n".join(
            f"{'✅' if x['ok'] else '❌'} {x['step']}" + (f": {x['detail']}" if not x["ok"] else "")
            for x in report["steps"]
        )
    )
    print("\nLIVE TEST " + ("PASSED" if report["ok"] else "FAILED"))
    return 0 if report["ok"] else 1
