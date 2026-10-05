"""Article / blog destinations — each gives a permanent page containing the backlink."""
from __future__ import annotations

import time
from html.parser import HTMLParser
from typing import Any

from requests_oauthlib import OAuth1Session

from ..content import Article
from . import register
from .base import Publisher, PublishError, PublishResult


# ---------------------------------------------------------------- Telegraph (no account needed)

_TG_ALLOWED = {
    "a", "aside", "b", "blockquote", "br", "code", "em", "figcaption", "figure", "h3", "h4", "hr",
    "i", "iframe", "img", "li", "ol", "p", "pre", "s", "strong", "u", "ul", "video",
}
_TG_REMAP = {"h1": "h3", "h2": "h3", "h5": "h4", "h6": "h4", "del": "s"}


class _TelegraphHTML(HTMLParser):
    """Convert HTML into Telegraph's Node JSON (list of tags/strings)."""

    def __init__(self) -> None:
        super().__init__()
        self.root: list[Any] = []
        self.stack: list[dict[str, Any]] = []

    def _append(self, node: Any) -> None:
        (self.stack[-1]["children"] if self.stack else self.root).append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = _TG_REMAP.get(tag, tag)
        if tag not in _TG_ALLOWED:
            return
        node: dict[str, Any] = {"tag": tag, "children": []}
        a = {k: v for k, v in attrs if k in ("href", "src") and v}
        if a:
            node["attrs"] = a
        self._append(node)
        if tag not in ("br", "hr", "img"):
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = _TG_REMAP.get(tag, tag)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._append(data)


def html_to_telegraph_nodes(html: str) -> list[Any]:
    p = _TelegraphHTML()
    p.feed(html)
    return p.root


@register
class Telegraph(Publisher):
    kind = "telegraph"
    API = "https://api.telegra.ph"

    def _token(self, c: Any) -> str:
        tok = self.opt("access_token")
        if tok:
            return tok
        r = c.post(f"{self.API}/createAccount", data={
            "short_name": self.opt("short_name", "author"),
            "author_name": self.opt("author_name", ""),
            "author_url": self.opt("author_url", ""),
        })
        d = self.check(r)
        if not d.get("ok"):
            raise PublishError(f"telegraph createAccount: {d}")
        self.o["access_token"] = d["result"]["access_token"]
        return self.o["access_token"]

    def publish_article(self, article: Article) -> PublishResult:
        with self.http() as c:
            token = self._token(c)
            r = c.post(f"{self.API}/createPage", json={
                "access_token": token,
                "title": article.title[:256],
                "author_name": self.opt("author_name", ""),
                "author_url": self.opt("author_url", ""),
                "content": html_to_telegraph_nodes(article.body_html),
                "return_content": False,
            })
            d = self.check(r)
            if not d.get("ok"):
                raise PublishError(f"telegraph createPage: {d}")
            return PublishResult(url=d["result"]["url"], external_id=d["result"]["path"], raw=d)


# ---------------------------------------------------------------- WordPress (self-hosted or wordpress.com)


@register
class WordPress(Publisher):
    """Self-hosted: base_url + username + app_password (Users → Application Passwords).
    wordpress.com: site (e.g. myblog.wordpress.com) + token (OAuth2 bearer)."""

    kind = "wordpress"

    def publish_article(self, article: Article) -> PublishResult:
        body = {
            "title": article.title,
            "content": article.body_html,
            "excerpt": article.excerpt,
            "status": self.opt("status", "publish"),
        }
        if self.opt("category_id"):
            body["categories"] = [int(self.opt("category_id"))]
        if self.opt("token"):
            url = f"https://public-api.wordpress.com/wp/v2/sites/{self.opt('site', required=True)}/posts"
            with self.http(headers={"Authorization": f"Bearer {self.opt('token')}"}) as c:
                d = self.check(c.post(url, json=body))
        else:
            base = self.opt("base_url", required=True).rstrip("/")
            auth = (self.opt("username", required=True), self.opt("app_password", required=True))
            with self.http(auth=auth) as c:
                d = self.check(c.post(f"{base}/wp-json/wp/v2/posts", json=body))
        return PublishResult(url=d.get("link", ""), external_id=str(d.get("id", "")), raw=d)


# ---------------------------------------------------------------- Blogger (Google)


@register
class Blogger(Publisher):
    kind = "blogger"

    def _access_token(self, c: Any) -> str:
        r = c.post("https://oauth2.googleapis.com/token", data={
            "client_id": self.opt("client_id", required=True),
            "client_secret": self.opt("client_secret", required=True),
            "refresh_token": self.opt("refresh_token", required=True),
            "grant_type": "refresh_token",
        })
        return self.check(r)["access_token"]

    def publish_article(self, article: Article) -> PublishResult:
        blog_id = self.opt("blog_id", required=True)
        with self.http() as c:
            tok = self._access_token(c)
            r = c.post(
                f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/",
                headers={"Authorization": f"Bearer {tok}"},
                json={"kind": "blogger#post", "title": article.title, "content": article.body_html, "labels": article.tags},
            )
            d = self.check(r)
        return PublishResult(url=d.get("url", ""), external_id=str(d.get("id", "")), raw=d)


# ---------------------------------------------------------------- Dev.to


@register
class DevTo(Publisher):
    kind = "devto"

    def publish_article(self, article: Article) -> PublishResult:
        tags = [t.replace(" ", "").lower() for t in article.tags if t.isascii()][:4]
        with self.http(headers={"api-key": self.opt("api_key", required=True)}) as c:
            d = self.check(c.post("https://dev.to/api/articles", json={"article": {
                "title": article.title, "body_markdown": article.body_markdown, "published": True, "tags": tags,
            }}))
        return PublishResult(url=d.get("url", ""), external_id=str(d.get("id", "")), raw=d)


# ---------------------------------------------------------------- Hashnode


@register
class Hashnode(Publisher):
    kind = "hashnode"
    QUERY = """mutation($input: PublishPostInput!) { publishPost(input: $input) { post { id url } } }"""

    def publish_article(self, article: Article) -> PublishResult:
        variables = {"input": {
            "title": article.title,
            "contentMarkdown": article.body_markdown,
            "publicationId": self.opt("publication_id", required=True),
            "tags": [{"name": t, "slug": t.lower().replace(" ", "-")} for t in article.tags[:5] if t.isascii()],
        }}
        with self.http(headers={"Authorization": self.opt("token", required=True)}) as c:
            d = self.check(c.post("https://gql.hashnode.com", json={"query": self.QUERY, "variables": variables}))
        if d.get("errors"):
            raise PublishError(f"hashnode: {d['errors']}")
        post = d["data"]["publishPost"]["post"]
        return PublishResult(url=post["url"], external_id=post["id"], raw=d)


# ---------------------------------------------------------------- Tumblr (OAuth1)


@register
class Tumblr(Publisher):
    kind = "tumblr"

    def publish_article(self, article: Article) -> PublishResult:
        blog = self.opt("blog", required=True)  # e.g. myblog.tumblr.com
        s = OAuth1Session(
            self.opt("consumer_key", required=True), self.opt("consumer_secret", required=True),
            self.opt("token", required=True), self.opt("token_secret", required=True),
        )
        if self.proxies():
            s.proxies = self.proxies()
        r = s.post(f"https://api.tumblr.com/v2/blog/{blog}/post", data={
            "type": "text", "title": article.title, "body": article.body_html,
            "tags": ",".join(article.tags), "format": "html",
        }, timeout=60)
        if r.status_code >= 400:
            raise PublishError(f"tumblr HTTP {r.status_code}: {r.text[:500]}")
        d = r.json()
        pid = str(d.get("response", {}).get("id", ""))
        return PublishResult(url=f"https://{blog}/post/{pid}", external_id=pid, raw=d)


# ---------------------------------------------------------------- Medium


@register
class Medium(Publisher):
    kind = "medium"

    def publish_article(self, article: Article) -> PublishResult:
        headers = {"Authorization": f"Bearer {self.opt('token', required=True)}"}
        with self.http(headers=headers) as c:
            user_id = self.opt("user_id") or self.check(c.get("https://api.medium.com/v1/me"))["data"]["id"]
            d = self.check(c.post(f"https://api.medium.com/v1/users/{user_id}/posts", json={
                "title": article.title, "contentFormat": "markdown", "content": f"# {article.title}\n\n{article.body_markdown}",
                "tags": article.tags[:5], "publishStatus": self.opt("publish_status", "public"),
            }))
        return PublishResult(url=d["data"]["url"], external_id=d["data"]["id"], raw=d)


# ---------------------------------------------------------------- Ghost (Admin API)


@register
class Ghost(Publisher):
    kind = "ghost"

    def _jwt(self) -> str:
        import jwt  # lazy: only Ghost needs PyJWT

        key_id, secret = self.opt("admin_api_key", required=True).split(":")
        now = int(time.time())
        return jwt.encode({"iat": now, "exp": now + 300, "aud": "/admin/"}, bytes.fromhex(secret),
                          algorithm="HS256", headers={"kid": key_id})

    def publish_article(self, article: Article) -> PublishResult:
        base = self.opt("url", required=True).rstrip("/")
        with self.http(headers={"Authorization": f"Ghost {self._jwt()}", "Accept-Version": "v5.0"}) as c:
            d = self.check(c.post(f"{base}/ghost/api/admin/posts/?source=html", json={"posts": [{
                "title": article.title, "html": article.body_html, "status": "published",
                "custom_excerpt": article.excerpt[:300], "tags": [{"name": t} for t in article.tags],
            }]}))
        post = d["posts"][0]
        return PublishResult(url=post.get("url", ""), external_id=post.get("id", ""), raw=d)


# ---------------------------------------------------------------- Write.as


@register
class WriteAs(Publisher):
    kind = "writeas"

    def publish_article(self, article: Article) -> PublishResult:
        alias = self.opt("collection", required=True)
        with self.http(headers={"Authorization": f"Token {self.opt('token', required=True)}"}) as c:
            d = self.check(c.post(f"https://write.as/api/collections/{alias}/posts",
                                  json={"title": article.title, "body": article.body_markdown}))
        data = d.get("data", {})
        return PublishResult(url=f"https://write.as/{alias}/{data.get('slug', data.get('id', ''))}",
                             external_id=data.get("id", ""), raw=d)
