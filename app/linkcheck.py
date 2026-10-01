"""Verify that a published page still contains our backlink, and whether it is dofollow."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .config import settings

log = logging.getLogger(__name__)


def _norm(u: str) -> str:
    p = urlparse(u)
    return (p.netloc.replace("www.", "").lower(), p.path.rstrip("/"))


def check_backlink(page_url: str, link_url: str) -> tuple[bool, str]:
    """Return (found, rel). rel is "" for dofollow, else the rel value (nofollow/ugc/sponsored)."""
    try:
        r = httpx.get(page_url, timeout=settings.http_timeout_sec, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; AutoBacklinkChecker/1.0)"})
    except httpx.HTTPError as e:
        log.warning("linkcheck %s: %s", page_url, e)
        return False, "fetch_error"
    if r.status_code >= 400:
        return False, f"http_{r.status_code}"
    soup = BeautifulSoup(r.text, "html.parser")
    want_host, want_path = _norm(link_url)
    best: tuple[bool, str] | None = None
    for a in soup.find_all("a", href=True):
        host, path = _norm(a["href"])
        if host == want_host and (path == want_path or not want_path):
            rel = " ".join(a.get("rel", [])) if isinstance(a.get("rel"), list) else (a.get("rel") or "")
            rel = rel.strip().lower()
            cand = (True, "" if rel in ("", "noopener", "noreferrer", "noopener noreferrer") else rel)
            if cand[1] == "":
                return cand
            best = best or cand
    return best or (False, "")
