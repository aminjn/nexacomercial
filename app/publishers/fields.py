"""Credential / option fields per account kind — drives the dashboard form and import validation.

Each entry: (name, required, hint). Every platform is used through its official API with your own
account's token; there is no username/password scraping, captcha solving or browser automation.
"""
from __future__ import annotations

from typing import Any

KIND_FIELDS: dict[str, list[tuple[str, bool, str]]] = {
    # ---- article / backlink destinations
    "telegraph": [("author_name", False, "نام نویسنده"), ("author_url", False, "لینک نویسنده"),
                  ("access_token", False, "خالی = ساخت خودکار حساب")],
    "wordpress": [("base_url", False, "https://myblog.com (self-hosted)"), ("username", False, ""),
                  ("app_password", False, "Users → Profile → Application Passwords"),
                  ("site", False, "myblog.wordpress.com (wordpress.com)"), ("token", False, "OAuth token برای wordpress.com"),
                  ("category_id", False, ""), ("status", False, "publish | draft")],
    "blogger": [("blog_id", True, ""), ("client_id", True, ""), ("client_secret", True, ""),
                ("refresh_token", True, "Google OAuth2 refresh token")],
    "devto": [("api_key", True, "Settings → Extensions → API Keys")],
    "hashnode": [("token", True, ""), ("publication_id", True, "")],
    "tumblr": [("blog", True, "myblog.tumblr.com"), ("consumer_key", True, ""), ("consumer_secret", True, ""),
               ("token", True, ""), ("token_secret", True, "")],
    "medium": [("token", True, "Integration token"), ("user_id", False, "")],
    "ghost": [("url", True, "https://myghost.com"), ("admin_api_key", True, "id:secret")],
    "writeas": [("collection", True, "alias"), ("token", True, "")],
    # ---- social networks
    "telegram": [("bot_token", True, "از BotFather"), ("chat_id", True, "@mychannel")],
    "x": [("consumer_key", True, ""), ("consumer_secret", True, ""), ("access_token", True, ""),
          ("access_token_secret", True, "")],
    "linkedin": [("access_token", True, ""), ("author_urn", True, "urn:li:person:... یا urn:li:organization:...")],
    "facebook": [("page_id", True, ""), ("page_access_token", True, "")],
    "instagram": [("ig_user_id", True, "اکانت بیزینس"), ("access_token", True, ""), ("image_url", False, "")],
    "threads": [("user_id", True, ""), ("access_token", True, "")],
    "mastodon": [("instance", False, "https://mastodon.social"), ("access_token", True, "")],
    "bluesky": [("handle", True, "me.bsky.social"), ("app_password", True, "Settings → App passwords")],
    "reddit": [("client_id", True, "script app"), ("client_secret", True, ""), ("username", True, ""),
               ("password", True, ""), ("subreddit", True, "ساب‌ردیت خودت یا جایی که لینک مجاز است"),
               ("post_type", False, "link | self")],
    "pinterest": [("access_token", True, ""), ("board_id", True, ""), ("image_url", False, "")],
    "webhook": [("url", True, "n8n / Make / Zapier / Buffer"), ("secret", False, "")],
}

SECRET_HINTS = ("token", "secret", "password", "key")


def is_secret(name: str) -> bool:
    return any(h in name for h in SECRET_HINTS)


def missing_fields(kind: str, creds: dict[str, Any]) -> list[str]:
    return [n for n, req, _ in KIND_FIELDS.get(kind, []) if req and not str(creds.get(n, "")).strip()]


def masked(creds: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in creds.items():
        v = str(v)
        out[k] = (v[:3] + "…" + v[-2:] if len(v) > 8 else "•••") if is_secret(k) and v else v
    return out
