"""Campaign tracking: UTM tags on every published link, short redirect links that count clicks,
and (optionally) each click forwarded to Google Analytics 4."""
from __future__ import annotations

import re
import secrets
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import settings


def slug(text: str) -> str:
    """utm_campaign value: readable, URL-safe (Persian letters are kept, spaces become dashes)."""
    text = unicodedata.normalize("NFC", text or "").strip().lower()
    return re.sub(r"[\s/?#&=]+", "-", text).strip("-")[:80] or "manual"


def new_code() -> str:
    return secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")


def tag(url: str, *, source: str, medium: str, campaign: str, content: str) -> str:
    """Add utm_* parameters (existing utm_* on the link are kept)."""
    if not url or not settings.utm_enabled:
        return url
    u = urlsplit(url)
    query = dict(parse_qsl(u.query, keep_blank_values=True))
    for k, v in (("utm_source", source), ("utm_medium", medium), ("utm_campaign", campaign), ("utm_content", content)):
        query.setdefault(k, v)
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(query), u.fragment))


def short_url(code: str) -> str:
    """Public short link for a social post, or "" when click counting is off / no public address is set."""
    if not (settings.click_redirect and settings.public_url and code):
        return ""
    return f"{settings.public_url.rstrip('/')}/r/{code}"


def retag_markdown(body: str, old: str, new: str) -> str:
    """Point markdown links at `old` to `new` (only the link target, never other URLs that merely start with it)."""
    if old == new:
        return body
    return re.sub(r"\]\(" + re.escape(old) + r"\)", f"]({new})", body)
