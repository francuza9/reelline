"""The credits command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reelline.config import conf
from reelline.pipeline import credits as _c
from reelline.pipeline import db
from reelline.pipeline.credits.base import CreditsError
from reelline.pipeline.credits.deploy import deploy_site, publish_page
from reelline.pipeline.credits.pages import build_site


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reelline.pipeline.credits", description=_c.__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=conf().storage.db_path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="render the site locally")
    p = sub.add_parser("deploy", help="snapshot a reel's credits, build, deploy, check")
    p.add_argument("reel_id", type=int)
    sub.add_parser("deploy-site", help="build and deploy the site as it is; check /privacy and /data-deletion")
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    try:
        if args.command == "build":
            for path in build_site(conn):
                print(path)
            return 0
        if args.command == "deploy-site":
            deploy_site(conn)
            return 0
        print(publish_page(conn, args.reel_id))
    except CreditsError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0
