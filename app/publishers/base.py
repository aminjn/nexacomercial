"""Publisher adapter base classes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..config import settings
from ..content import Article, SocialPost


@dataclass
class PublishResult:
    url: str = ""
    external_id: str = ""
    raw: Any = None


class PublishError(RuntimeError):
    pass


class Publisher:
    """Base class. `kind` is the YAML `kind`; `category` is "article" or "social"."""

    kind: str = ""
    category: str = "article"
    # Character budget for social posts (None = no practical limit).
    max_chars: int | None = None
    # Whether this destination needs an image (Instagram / Pinterest).
    needs_image: bool = False

    def __init__(self, options: dict[str, Any]) -> None:
        self.o = options

    # ---- helpers
    def opt(self, key: str, default: Any = None, required: bool = False) -> Any:
        v = self.o.get(key, default)
        if required and (v is None or v == ""):
            raise PublishError(f"{self.kind}: missing option '{key}'")
        return v

    def http(self, **kw: Any) -> httpx.Client:
        kw.setdefault("timeout", settings.http_timeout_sec)
        kw.setdefault("follow_redirects", True)
        return httpx.Client(**kw)

    @staticmethod
    def check(r: httpx.Response) -> dict[str, Any]:
        if r.status_code >= 400:
            raise PublishError(f"HTTP {r.status_code}: {r.text[:500]}")
        try:
            return r.json()
        except ValueError:
            return {}

    # ---- API
    def publish_article(self, article: Article) -> PublishResult:
        raise PublishError(f"{self.kind} does not publish articles")

    def publish_social(self, post: SocialPost) -> PublishResult:
        raise PublishError(f"{self.kind} does not publish social posts")
