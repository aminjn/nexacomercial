"""Post statistics (views / likes / comments / shares) for every platform that makes them available:
public APIs and pages where possible, the logged-in browser for Instagram."""
from __future__ import annotations

import datetime as dt
import logging
import re
from collections import defaultdict
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import httpx

from sqlmodel import select

from . import browser, v2ray
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


# ---------------------------------------------------------------- other platforms: public APIs / pages

Stats = dict[str, int]


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True, proxy=v2ray.publish_proxy() or None,
                        headers={"User-Agent": "Mozilla/5.0 (compatible; NexaStats/1.0)"})


def _telegram(c: httpx.Client, p: m.Publication) -> Stats | None:
    found = re.match(r"https?://t\.me/([\w_]+)/(\d+)", p.url)
    if not found:
        return None
    html = c.get(f"https://t.me/{found.group(1)}/{found.group(2)}?embed=1").text
    views = re.search(r'tgme_widget_message_views">([\d.,]+\s*[KkMm]?)<', html)
    return {"views": parse_count(views.group(1))} if views else None


def _telegraph(c: httpx.Client, p: m.Publication) -> Stats | None:
    path = p.external_id or urlsplit(p.url).path.strip("/")
    d = c.get(f"https://api.telegra.ph/getViews/{quote(path)}").json()
    return {"views": int(d["result"]["views"])} if d.get("ok") else None


def _devto(c: httpx.Client, p: m.Publication) -> Stats | None:
    parts = urlsplit(p.url).path.strip("/").split("/")
    if len(parts) != 2:
        return None
    d = c.get(f"https://dev.to/api/articles/{parts[0]}/{parts[1]}").json()
    return {"likes": int(d.get("public_reactions_count", 0)), "comments": int(d.get("comments_count", 0))}


def _mastodon(c: httpx.Client, p: m.Publication) -> Stats | None:
    u = urlsplit(p.url)
    sid = u.path.rstrip("/").rsplit("/", 1)[-1]
    if not sid.isdigit():
        return None
    d = c.get(f"{u.scheme}://{u.netloc}/api/v1/statuses/{sid}").json()
    return {"likes": int(d.get("favourites_count", 0)), "comments": int(d.get("replies_count", 0)),
            "shares": int(d.get("reblogs_count", 0))}


def _bluesky(c: httpx.Client, p: m.Publication) -> Stats | None:
    if not p.external_id.startswith("at://"):
        return None
    posts = c.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts", params={"uris": p.external_id}).json()
    d = (posts.get("posts") or [{}])[0]
    return {"likes": int(d.get("likeCount", 0)), "comments": int(d.get("replyCount", 0)),
            "shares": int(d.get("repostCount", 0))} if d else None


def _reddit(c: httpx.Client, p: m.Publication) -> Stats | None:
    if "/comments/" not in p.url:
        return None
    j = c.get(p.url.split("?")[0].rstrip("/") + ".json").json()
    d = j[0]["data"]["children"][0]["data"]
    return {"likes": int(d.get("score", 0)), "comments": int(d.get("num_comments", 0))}


def _writeas(c: httpx.Client, p: m.Publication) -> Stats | None:
    if not p.external_id:
        return None
    d = c.get(f"https://write.as/api/posts/{p.external_id}").json().get("data", {})
    return {"views": int(d["views"])} if "views" in d else None


def _wordpress(c: httpx.Client, p: m.Publication) -> Stats | None:
    u = urlsplit(p.url)
    if not p.external_id.isdigit():
        return None
    r = c.get(f"{u.scheme}://{u.netloc}/wp-json/wp/v2/comments", params={"post": p.external_id, "per_page": 1})
    return {"comments": int(r.headers["X-WP-Total"])} if r.status_code == 200 and "X-WP-Total" in r.headers else None


def _blogger(c: httpx.Client, p: m.Publication) -> Stats | None:
    u = urlsplit(p.url)
    if not p.external_id:
        return None
    d = c.get(f"{u.scheme}://{u.netloc}/feeds/{p.external_id}/comments/default",
              params={"alt": "json", "max-results": 0}).json()
    return {"comments": int(d["feed"]["openSearch$totalResults"]["$t"])}


FETCHERS: dict[str, Callable[[httpx.Client, m.Publication], Stats | None]] = {
    "telegram": _telegram, "telegraph": _telegraph, "devto": _devto, "mastodon": _mastodon, "bluesky": _bluesky,
    "reddit": _reddit, "writeas": _writeas, "wordpress": _wordpress, "blogger": _blogger,
}


def base_kind(kind: str) -> str:
    return kind.removesuffix("_web")


def has_stats(kind: str) -> bool:
    return base_kind(kind) in FETCHERS or base_kind(kind) == "instagram"


def refresh_public(max_age_hours: float = 6, site_id: int | None = None) -> int:
    cutoff = m.utcnow() - dt.timedelta(hours=max_age_hours)
    with m.session() as s:
        q = select(m.Publication).where(m.Publication.status == "ok", m.Publication.url != "")
        if site_id:
            q = q.where(m.Publication.site_id == site_id)
        rows = [p for p in s.exec(q).all()
                if base_kind(p.account_kind) in FETCHERS and not (p.stats_at and p.stats_at > cutoff)]
    updated = 0
    with _client() as c:
        for p in rows:
            try:
                val = FETCHERS[base_kind(p.account_kind)](c, p)
            except Exception as e:  # noqa: BLE001 — a deleted post or a changed page must not stop the rest
                log.info("stats %s %s: %s", p.account_kind, p.url, e)
                val = None
            with m.session() as s:
                row = s.get(m.Publication, p.id)
                row.stats_at = m.utcnow()
                for k, v in (val or {}).items():
                    setattr(row, k, v)
                s.add(row)
                s.commit()
            updated += bool(val)
    return updated


def refresh_all(max_age_hours: float = 6, site_id: int | None = None) -> int:
    """Every platform: public stats + Instagram through the browser. Returns how many posts got numbers."""
    return refresh_public(max_age_hours, site_id) + refresh_instagram(max_age_hours, site_id)
