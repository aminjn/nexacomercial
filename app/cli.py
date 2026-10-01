"""Command-line control.  python -m app.cli --help"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlmodel import select

from . import engine, importer
from . import models as m
from .publishers import REGISTRY


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="nexa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("kinds", help="list supported account kinds")
    p = sub.add_parser("import-accounts", help="bulk import accounts from a CSV/JSON file ('-' = stdin)")
    p.add_argument("file")
    p.add_argument("--tag", default="")
    p = sub.add_parser("import-sites", help="bulk import sites from a CSV/JSON file")
    p.add_argument("file")
    p = sub.add_parser("tick", help="run every due campaign now")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("run", help="run one step of a campaign now (ignores window/limits)")
    p.add_argument("campaign_id", type=int)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("test", help="publish one real item through an account")
    p.add_argument("account_id", type=int)
    p.add_argument("--site", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("verify", help="re-check all backlinks")
    p = sub.add_parser("list", help="recent publications")
    p.add_argument("--limit", type=int, default=30)

    a = ap.parse_args()
    m.engine()
    if a.cmd == "kinds":
        for k in sorted(REGISTRY):
            print(f"{k:12s} {REGISTRY[k].category}")
    elif a.cmd in ("import-accounts", "import-sites"):
        text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8-sig").read()
        n, errors = importer.import_accounts(text, a.tag) if a.cmd == "import-accounts" else importer.import_sites(text)
        print(f"imported: {n}")
        for e in errors:
            print("  !", e)
    elif a.cmd == "tick":
        for r in engine.tick(dry_run=True if a.dry_run else None):
            print(f"[{r.status}] {r.account_label}: {r.title}\n    {r.url or r.error}")
    elif a.cmd == "run":
        r = engine.run_campaign(a.campaign_id, force=True, dry_run=True if a.dry_run else None)
        print("no ready account" if r is None else f"[{r.status}] {r.account_label}: {r.url or r.error}\n{r.body_preview}")
    elif a.cmd == "test":
        r = engine.test_account(a.account_id, a.site or None, dry_run=a.dry_run)
        print(f"[{r.status}] {r.url or r.error}")
    elif a.cmd == "verify":
        print("checked:", engine.verify_links(max_age_hours=0))
    elif a.cmd == "list":
        with m.session() as s:
            for r in s.exec(select(m.Publication).order_by(m.Publication.created_at.desc()).limit(a.limit)):
                live = {True: "live", False: "MISSING", None: "?"}[r.link_found]
                print(f"{r.created_at:%Y-%m-%d %H:%M} {r.status:7s} site#{r.site_id:<4} {r.account_label[:16]:16s} "
                      f"{live:7s} {r.url or r.error[:60]}")


if __name__ == "__main__":
    main()
