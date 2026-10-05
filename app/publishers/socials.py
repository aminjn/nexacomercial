"""Social network destinations."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from requests_oauthlib import OAuth1Session

from ..content import SocialPost
from . import register
from .base import Publisher, PublishError, PublishResult


# ---------------------------------------------------------------- Telegram channel / group


@register
class Telegram(Publisher):
    kind = "telegram"
    category = "social"
    max_chars = 3500

    def publish_social(self, post: SocialPost) -> PublishResult:
        token = self.opt("bot_token", required=True)
        chat_id = self.opt("chat_id", required=True)
        text = post.render(self.max_chars)
        with self.http() as c:
            d = self.check(c.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat_id, "text": text, "disable_web_page_preview": False,
            }))
        if not d.get("ok"):
            raise PublishError(f"telegram: {d}")
        res = d["result"]
        username = res.get("chat", {}).get("username")
        mid = str(res.get("message_id", ""))
        url = f"https://t.me/{username}/{mid}" if username else ""
        return PublishResult(url=url, external_id=mid, raw=d)


# ---------------------------------------------------------------- X / Twitter (OAuth 1.0a user context)


@register
class XTwitter(Publisher):
    kind = "x"
    category = "social"
    max_chars = 280

    def publish_social(self, post: SocialPost) -> PublishResult:
        s = OAuth1Session(
            self.opt("consumer_key", required=True), self.opt("consumer_secret", required=True),
            self.opt("access_token", required=True), self.opt("access_token_secret", required=True),
        )
        if self.proxies():
            s.proxies = self.proxies()
        # URLs count as 23 chars on X regardless of length.
        text = post.render(self.max_chars - 23 + len(post.link_url))
        r = s.post("https://api.twitter.com/2/tweets", json={"text": text}, timeout=60)
        if r.status_code >= 400:
            raise PublishError(f"x HTTP {r.status_code}: {r.text[:500]}")
        d = r.json()
        tid = d.get("data", {}).get("id", "")
        return PublishResult(url=f"https://x.com/i/status/{tid}", external_id=tid, raw=d)


# ---------------------------------------------------------------- LinkedIn (person or organization)


@register
class LinkedIn(Publisher):
    kind = "linkedin"
    category = "social"
    max_chars = 2900

    def publish_social(self, post: SocialPost) -> PublishResult:
        author = self.opt("author_urn", required=True)  # urn:li:person:xxx or urn:li:organization:xxx
        headers = {"Authorization": f"Bearer {self.opt('access_token', required=True)}",
                   "X-Restli-Protocol-Version": "2.0.0"}
        body = {
            "author": author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": post.render(self.max_chars, with_hashtags=True)},
                "shareMediaCategory": "ARTICLE",
                "media": [{"status": "READY", "originalUrl": post.link_url, "title": {"text": post.title or post.link_url}}],
            }},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        with self.http(headers=headers) as c:
            r = c.post("https://api.linkedin.com/v2/ugcPosts", json=body)
            d = self.check(r)
            urn = r.headers.get("x-restli-id") or d.get("id", "")
        return PublishResult(url=f"https://www.linkedin.com/feed/update/{urn}", external_id=urn, raw=d)


# ---------------------------------------------------------------- Facebook Page


@register
class FacebookPage(Publisher):
    kind = "facebook"
    category = "social"
    max_chars = 5000

    def publish_social(self, post: SocialPost) -> PublishResult:
        page_id = self.opt("page_id", required=True)
        v = self.opt("api_version", "v19.0")
        with self.http() as c:
            d = self.check(c.post(f"https://graph.facebook.com/{v}/{page_id}/feed", data={
                "message": post.render(self.max_chars, with_hashtags=True),
                "link": post.link_url,
                "access_token": self.opt("page_access_token", required=True),
            }))
        pid = d.get("id", "")
        return PublishResult(url=f"https://www.facebook.com/{pid}", external_id=pid, raw=d)


# ---------------------------------------------------------------- Instagram (business account, needs an image)


@register
class Instagram(Publisher):
    kind = "instagram"
    category = "social"
    max_chars = 2000
    needs_image = True

    def publish_social(self, post: SocialPost) -> PublishResult:
        image = post.image_url or self.opt("image_url")
        if not image:
            raise PublishError("instagram needs an image_url (site.image_url or target image_url)")
        user = self.opt("ig_user_id", required=True)
        tok = self.opt("access_token", required=True)
        v = self.opt("api_version", "v19.0")
        with self.http() as c:
            d = self.check(c.post(f"https://graph.facebook.com/{v}/{user}/media", data={
                "image_url": image, "caption": post.render(self.max_chars), "access_token": tok}))
            d2 = self.check(c.post(f"https://graph.facebook.com/{v}/{user}/media_publish",
                                   data={"creation_id": d["id"], "access_token": tok}))
            mid = d2.get("id", "")
            d3 = self.check(c.get(f"https://graph.facebook.com/{v}/{mid}", params={"fields": "permalink", "access_token": tok}))
        return PublishResult(url=d3.get("permalink", ""), external_id=mid, raw=d3)


# ---------------------------------------------------------------- Threads


@register
class Threads(Publisher):
    kind = "threads"
    category = "social"
    max_chars = 500

    def publish_social(self, post: SocialPost) -> PublishResult:
        user = self.opt("user_id", required=True)
        tok = self.opt("access_token", required=True)
        with self.http() as c:
            d = self.check(c.post(f"https://graph.threads.net/v1.0/{user}/threads", data={
                "media_type": "TEXT", "text": post.render(self.max_chars), "access_token": tok}))
            d2 = self.check(c.post(f"https://graph.threads.net/v1.0/{user}/threads_publish",
                                   data={"creation_id": d["id"], "access_token": tok}))
            mid = d2.get("id", "")
            d3 = self.check(c.get(f"https://graph.threads.net/v1.0/{mid}", params={"fields": "permalink", "access_token": tok}))
        return PublishResult(url=d3.get("permalink", ""), external_id=mid, raw=d3)


# ---------------------------------------------------------------- Mastodon (any instance)


@register
class Mastodon(Publisher):
    kind = "mastodon"
    category = "social"
    max_chars = 480

    def publish_social(self, post: SocialPost) -> PublishResult:
        base = self.opt("instance", "https://mastodon.social").rstrip("/")
        with self.http(headers={"Authorization": f"Bearer {self.opt('access_token', required=True)}"}) as c:
            d = self.check(c.post(f"{base}/api/v1/statuses", json={"status": post.render(self.max_chars)}))
        return PublishResult(url=d.get("url", ""), external_id=str(d.get("id", "")), raw=d)


# ---------------------------------------------------------------- Bluesky (AT Protocol)

_URL_RE = re.compile(r"https?://[^\s]+")


def bluesky_facets(text: str) -> list[dict[str, Any]]:
    """Link facets with UTF-8 byte offsets so URLs are clickable."""
    facets = []
    b = text.encode("utf-8")
    for m in _URL_RE.finditer(text):
        start = len(text[: m.start()].encode("utf-8"))
        end = start + len(m.group(0).encode("utf-8"))
        assert b[start:end].decode("utf-8") == m.group(0)
        facets.append({"index": {"byteStart": start, "byteEnd": end},
                       "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0)}]})
    return facets


@register
class Bluesky(Publisher):
    kind = "bluesky"
    category = "social"
    max_chars = 300

    def publish_social(self, post: SocialPost) -> PublishResult:
        base = self.opt("pds", "https://bsky.social").rstrip("/")
        handle = self.opt("handle", required=True)
        text = post.render(self.max_chars)
        with self.http() as c:
            sess = self.check(c.post(f"{base}/xrpc/com.atproto.server.createSession",
                                     json={"identifier": handle, "password": self.opt("app_password", required=True)}))
            record = {"$type": "app.bsky.feed.post", "text": text, "facets": bluesky_facets(text),
                      "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")}
            d = self.check(c.post(f"{base}/xrpc/com.atproto.repo.createRecord",
                                  headers={"Authorization": f"Bearer {sess['accessJwt']}"},
                                  json={"repo": sess["did"], "collection": "app.bsky.feed.post", "record": record}))
        uri = d.get("uri", "")
        rkey = uri.rsplit("/", 1)[-1]
        return PublishResult(url=f"https://bsky.app/profile/{handle}/post/{rkey}", external_id=uri, raw=d)


# ---------------------------------------------------------------- Reddit (script app)


@register
class Reddit(Publisher):
    kind = "reddit"
    category = "social"
    max_chars = 300

    def publish_social(self, post: SocialPost) -> PublishResult:
        ua = self.opt("user_agent", "autobacklink/1.0")
        with self.http(headers={"User-Agent": ua}) as c:
            tok = self.check(c.post("https://www.reddit.com/api/v1/access_token",
                                    auth=(self.opt("client_id", required=True), self.opt("client_secret", required=True)),
                                    data={"grant_type": "password", "username": self.opt("username", required=True),
                                          "password": self.opt("password", required=True)}))
            if "access_token" not in tok:
                raise PublishError(f"reddit auth: {tok}")
            data = {"sr": self.opt("subreddit", required=True), "title": post.text[:300], "api_type": "json",
                    "resubmit": "true"}
            if self.opt("post_type", "link") == "link":
                data.update(kind="link", url=post.link_url)
            else:
                data.update(kind="self", text=post.render())
            d = self.check(c.post("https://oauth.reddit.com/api/submit",
                                  headers={"Authorization": f"Bearer {tok['access_token']}"}, data=data))
        j = d.get("json", {})
        if j.get("errors"):
            raise PublishError(f"reddit: {j['errors']}")
        return PublishResult(url=j.get("data", {}).get("url", ""), external_id=j.get("data", {}).get("id", ""), raw=d)


# ---------------------------------------------------------------- Pinterest (needs an image)


@register
class Pinterest(Publisher):
    kind = "pinterest"
    category = "social"
    max_chars = 500
    needs_image = True

    def publish_social(self, post: SocialPost) -> PublishResult:
        image = post.image_url or self.opt("image_url")
        if not image:
            raise PublishError("pinterest needs an image_url (site.image_url or target image_url)")
        with self.http(headers={"Authorization": f"Bearer {self.opt('access_token', required=True)}"}) as c:
            d = self.check(c.post("https://api.pinterest.com/v5/pins", json={
                "board_id": self.opt("board_id", required=True), "title": (post.title or post.text)[:100],
                "description": post.render(self.max_chars, with_hashtags=True), "link": post.link_url,
                "media_source": {"source_type": "image_url", "url": image},
            }))
        pid = d.get("id", "")
        return PublishResult(url=f"https://www.pinterest.com/pin/{pid}/", external_id=pid, raw=d)


# ---------------------------------------------------------------- Generic webhook (n8n / Make / Zapier / Buffer ...)


@register
class Webhook(Publisher):
    """POSTs the post (or article) as JSON. Use it to reach anything without a native adapter."""

    kind = "webhook"
    category = "social"

    def _send(self, payload: dict[str, Any]) -> PublishResult:
        headers = {"Content-Type": "application/json"}
        if self.opt("secret"):
            headers["X-Webhook-Secret"] = self.opt("secret")
        with self.http(headers=headers) as c:
            d = self.check(c.post(self.opt("url", required=True), json=payload))
        return PublishResult(url=str(d.get("url", "")), external_id=str(d.get("id", "")), raw=d)

    def publish_social(self, post: SocialPost) -> PublishResult:
        return self._send({"type": "social", "text": post.render(), "plain_text": post.text, "link": post.link_url,
                           "hashtags": post.hashtags, "image_url": post.image_url, "site_id": post.site_id})

    def publish_article(self, article: Any) -> PublishResult:
        return self._send({"type": "article", "title": article.title, "excerpt": article.excerpt, "tags": article.tags,
                           "body_markdown": article.body_markdown, "body_html": article.body_html,
                           "link": article.link_url, "anchor": article.anchor, "site_id": article.site_id})
