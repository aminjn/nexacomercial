"""Post statistics: Instagram likes / comments read through the account's logged-in browser."""
from __future__ import annotations

import datetime as dt
import logging
import re
from collections import defaultdict
from typing import Any

from sqlmodel import select

from . import browser
from . import models as m

log = logging.getLogger(__name__)

# the post page's <meta name="description">: "25 likes, 2 comments - melkjet on October 10, 2026: ..."
_STATS_RE = re.compile(r"([\d.,]+\s*[KkMm]?)\s+likes?,\s*([\d.,]+\s*[KkMm]?)\s+comments?", re.I)


def parse_count(s: str) -> int:
    s = s.strip().replace(",", "")
    mult = {"k": 1_000, "m": 1_000_000}.get(s[-1:].lower(), 1)
    return int(float(s.rstrip("KkMm ").strip() or 0) * mult)


def parse_meta(text: str) -> tuple[int, int] | None:
    found = _STATS_RE.search(text or "")
    return (parse_count(found.group(1)), parse_count(found.group(2))) if found else None


def is_post_url(url: str) -> bool:
    return "instagram.com/p/" in url or "instagram.com/reel/" in url


def refresh_instagram(max_age_hours: float = 6, site_id: int | None = None) -> int:
    """Update likes / comments of published Instagram posts whose address is known. Returns how many."""
    cutoff = m.utcnow() - dt.timedelta(hours=max_age_hours)
    with m.session() as s:
        q = select(m.Publication).where(m.Publication.status == "ok", m.Publication.account_kind == "instagram_web")
        if site_id:
            q = q.where(m.Publication.site_id == site_id)
        rows = [p for p in s.exec(q).all() if is_post_url(p.url) and not (p.stats_at and p.stats_at > cutoff)]
    by_account: dict[int, list[m.Publication]] = defaultdict(list)
    for p in rows:
        by_account[p.account_id].append(p)

    updated = 0
    for account_id, pubs in by_account.items():
        if not browser.has_session(account_id):
            continue

        def recipe(page: Any, pubs: list[m.Publication] = pubs) -> dict[int, tuple[int, int] | None]:
            out: dict[int, tuple[int, int] | None] = {}
            for p in pubs:
                try:
                    page.goto(p.url, wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(2000)
                    meta = (page.locator('meta[name="description"]').first.get_attribute("content", timeout=10_000)
                            or page.locator('meta[property="og:description"]').first.get_attribute("content", timeout=5_000)
                            or "")
                    out[p.id] = parse_meta(meta)
                except Exception as e:  # noqa: BLE001 — one broken post must not stop the rest
                    log.warning("instagram stats %s: %s", p.url, e)
            return out

        try:
            results = browser.run_with_session(account_id, recipe, timeout=60 + 90 * len(pubs), keep_shot=False)
        except Exception as e:  # noqa: BLE001
            log.warning("instagram stats for account %s failed: %s", account_id, e)
            continue
        with m.session() as s:
            for pid, val in results.items():
                p = s.get(m.Publication, pid)
                if p is None:
                    continue
                p.stats_at = m.utcnow()
                if val:
                    p.likes, p.comments = val
                    updated += 1
                s.add(p)
            s.commit()
    return updated
