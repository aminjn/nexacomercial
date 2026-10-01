import json

import httpx
import pytest

from app.content import Article, SocialPost
from app.publishers import REGISTRY, make
from app.publishers.articles import html_to_telegraph_nodes
from app.publishers.base import Publisher
from app.publishers.socials import bluesky_facets


def _mock(handler):
    """Patch Publisher.http so every adapter talks to an httpx MockTransport."""
    def http(self, **kw):
        kw.pop("timeout", None)
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)
    return http


ART = Article(title="T", body_markdown="hi [a](https://example.com) there", excerpt="e", tags=["x"],
              link_url="https://example.com", anchor="a", site_id=1)
POST = SocialPost(text="hello", link_url="https://example.com", hashtags=["tag"], title="T", site_id=1)


def test_registry_has_all_kinds():
    expected = {"telegraph", "wordpress", "blogger", "devto", "hashnode", "tumblr", "medium", "ghost", "writeas",
                "telegram", "x", "linkedin", "facebook", "instagram", "threads", "mastodon", "bluesky", "reddit",
                "pinterest", "webhook"}
    assert expected <= set(REGISTRY)


def test_telegraph_nodes_conversion():
    nodes = html_to_telegraph_nodes('<h2>H</h2><p>hi <a href="https://e.com">l</a><script>x</script></p>')
    assert nodes[0] == {"tag": "h3", "children": ["H"]}
    assert nodes[1]["tag"] == "p" and nodes[1]["children"][1] == {"tag": "a", "attrs": {"href": "https://e.com"}, "children": ["l"]}


def test_telegraph_publish(monkeypatch):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path == "/createAccount":
            return httpx.Response(200, json={"ok": True, "result": {"access_token": "tok"}})
        body = json.loads(req.content)
        assert body["access_token"] == "tok" and body["title"] == "T"
        return httpx.Response(200, json={"ok": True, "result": {"url": "https://telegra.ph/T-1", "path": "T-1"}})

    monkeypatch.setattr(Publisher, "http", _mock(handler))
    res = make("telegraph", {"author_name": "x"}).publish_article(ART)
    assert res.url == "https://telegra.ph/T-1" and calls == ["/createAccount", "/createPage"]


def test_wordpress_selfhosted(monkeypatch):
    def handler(req):
        assert req.url.path == "/wp-json/wp/v2/posts" and "Authorization" in req.headers
        return httpx.Response(201, json={"id": 7, "link": "https://blog.test/t"})
    monkeypatch.setattr(Publisher, "http", _mock(handler))
    res = make("wordpress", {"base_url": "https://blog.test", "username": "u", "app_password": "p"}).publish_article(ART)
    assert res.url == "https://blog.test/t" and res.external_id == "7"


def test_telegram_social(monkeypatch):
    def handler(req):
        body = json.loads(req.content)
        assert body["chat_id"] == "@c" and "https://example.com" in body["text"] and "#tag" in body["text"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5, "chat": {"username": "c"}}})
    monkeypatch.setattr(Publisher, "http", _mock(handler))
    res = make("telegram", {"bot_token": "t", "chat_id": "@c"}).publish_social(POST)
    assert res.url == "https://t.me/c/5"


def test_http_error_becomes_publish_error(monkeypatch):
    monkeypatch.setattr(Publisher, "http", _mock(lambda req: httpx.Response(401, text="nope")))
    with pytest.raises(Exception) as ei:
        make("mastodon", {"access_token": "t"}).publish_social(POST)
    assert "401" in str(ei.value)


def test_bluesky_facets_byte_offsets():
    text = "سلام https://ex.com/a پایان"
    f = bluesky_facets(text)
    assert len(f) == 1
    b = text.encode()
    assert b[f[0]["index"]["byteStart"]:f[0]["index"]["byteEnd"]].decode() == "https://ex.com/a"


def test_missing_option_is_reported():
    with pytest.raises(Exception, match="missing option"):
        make("devto", {}).publish_article(ART)
