"""Browser accounts: live login + recipes against fake copies of the sites (real Chromium, no network)."""
import pytest

from app import browser
from app import models as m
from app.content import Article, SocialPost
from app.publishers import make


def _chromium_ok() -> bool:
    try:
        browser.call(browser._get_browser, timeout=60)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _chromium_ok(), reason="Chromium not available")

LOGIN = """<html><body><form onsubmit="document.cookie='sid=ok; path=/; max-age=9999'; location.href='/home'; return false">
<input id="u" style="position:absolute;left:10px;top:10px;width:300px;height:40px">
<button>go</button></form></body></html>"""
PAGES: dict[str, str] = {}
POSTED: list[dict] = []


@pytest.fixture(autouse=True)
def fake_sites(monkeypatch):
    PAGES.clear()
    POSTED.clear()
    import shutil
    shutil.rmtree(browser.settings.data_path / "sessions", ignore_errors=True)
    orig = browser.new_context

    def ctx_with_routes(state=None):
        ctx = orig(state)

        def handle(route):
            req = route.request
            if req.method == "POST":
                POSTED.append({"url": req.url, "body": req.post_data})
            key = next((k for k in sorted(PAGES, key=len, reverse=True) if req.url.startswith(k)), None)
            if key is None:
                return route.fulfill(status=404, body="")
            return route.fulfill(status=200, content_type="text/html", body=PAGES[key])
        ctx.route("**/*", handle)
        return ctx
    monkeypatch.setattr(browser, "new_context", ctx_with_routes)
    yield
    browser.call(lambda: [browser._close_login(a) for a in list(browser._logins)])


def account(kind: str, **creds) -> m.Account:
    a = m.Account(label="t", kind=kind, category=make(kind, {}).category)
    a.creds = creds
    with m.session() as s:
        s.add(a)
        s.commit()
        s.refresh(a)
    return a


def logged_in(a: m.Account, domain: str) -> None:
    browser.save_state(a.id, {"cookies": [{"name": "sid", "value": "ok", "domain": domain, "path": "/",
                                           "expires": -1, "httpOnly": False, "secure": True, "sameSite": "Lax"}],
                              "origins": []})


def test_live_login_saves_session(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.publishers.web import XWeb
    PAGES["https://x.com/i/flow/login"] = LOGIN
    PAGES["https://x.com/home"] = "<h1>home</h1>"
    a = account("x_web", handle="me")
    with TestClient(app) as client:
        assert "ورود با مرورگر" in client.get("/accounts").text
        r = client.get(f"/accounts/{a.id}/login")
        assert r.status_code == 200 and "ورود به" in r.text
        assert client.get(f"/accounts/{a.id}/login/shot").content[:2] == b"\xff\xd8"  # JPEG
        assert client.post(f"/accounts/{a.id}/login/act", json={"action": "click", "x": 50, "y": 30}).json()["ok"]
        assert client.post(f"/accounts/{a.id}/login/act", json={"action": "type", "text": "me@example.com"}).json()["ok"]
        r = client.post(f"/accounts/{a.id}/login/act", json={"action": "key", "key": "Enter"}).json()
        assert r["ok"] and r["url"] == "https://x.com/home"
        assert "ذخیره شد" in client.post(f"/accounts/{a.id}/login/finish").text
        assert not browser.login_active(a.id)
        assert client.get(f"/accounts/{a.id}/login/shot").status_code == 410
    state = browser.load_state(a.id)
    assert any(c["name"] == "sid" for c in state["cookies"])
    assert XWeb.login_url


def test_not_logged_in_and_expired():
    from app.publishers.base import PublishError
    a = account("reddit_web", subreddit="test")
    post = SocialPost(text="hi", link_url="https://example.com/")
    with pytest.raises(PublishError, match="ورود با مرورگر"):
        make("reddit_web", {**a.creds, "_account_id": a.id}).publish_social(post)
    logged_in(a, ".reddit.com")
    PAGES["https://old.reddit.com/r/test/submit"] = "<script>location.href='https://old.reddit.com/login'</script>"
    PAGES["https://old.reddit.com/login"] = "<form id='login-form'></form>"
    with pytest.raises(PublishError, match="منقضی"):
        make("reddit_web", {**a.creds, "_account_id": a.id}).publish_social(post)


def test_x_recipe():
    a = account("x_web")
    logged_in(a, ".x.com")
    PAGES["https://x.com/compose/post"] = """<div data-testid="tweetTextarea_0" contenteditable="true" id="t"></div>
      <button data-testid="tweetButton" onclick="fetch('/post',{method:'POST',body:document.getElementById('t').innerText});
        document.getElementById('t').remove();
        document.body.insertAdjacentHTML('beforeend','<div data-testid=toast><a href=/me/status/123>View</a></div>')">Post</button>"""
    PAGES["https://x.com/post"] = "ok"
    res = make("x_web", {"_account_id": a.id}).publish_social(SocialPost(text="سلام دنیا", link_url="https://example.com/p"))
    assert res.url == "https://x.com/me/status/123"
    assert "سلام دنیا" in POSTED[0]["body"] and "https://example.com/p" in POSTED[0]["body"]


def test_reddit_recipe():
    a = account("reddit_web", subreddit="r/test")
    logged_in(a, ".reddit.com")
    PAGES["https://old.reddit.com/r/test/submit"] = """<form id="newlink" method="post" action="/r/test/comments/abc/t/">
      <button type="submit" name="submit">submit</button></form>"""
    PAGES["https://old.reddit.com/r/test/comments/"] = "<h1>posted</h1>"
    res = make("reddit_web", {**a.creds, "_account_id": a.id}).publish_social(
        SocialPost(text="hello", title="Title", link_url="https://example.com/"))
    assert res.url == "https://old.reddit.com/r/test/comments/abc/t/"


def test_devto_recipe():
    a = account("devto_web")
    logged_in(a, ".dev.to")
    PAGES["https://dev.to/new"] = """<form method="post" action="/me/my-post-1"><input id="article-form-title" name="t">
      <textarea id="article_body_markdown" name="b"></textarea><button>Publish</button></form>"""
    PAGES["https://dev.to/me/"] = "<h1>post</h1>"
    art = Article(title="T", body_markdown="## H\n\nbody [link](https://example.com)", excerpt="", tags=[],
                  link_url="https://example.com", anchor="link", site_id=1)
    res = make("devto_web", {"_account_id": a.id}).publish_article(art)
    assert res.url == "https://dev.to/me/my-post-1"
    assert "t=T" in POSTED[0]["body"] and "example.com" in POSTED[0]["body"]


def test_mastodon_recipe(monkeypatch):
    import httpx
    a = account("mastodon_web", instance="mastodon.example")
    logged_in(a, "mastodon.example")
    PAGES["https://mastodon.example/home"] = '<script id="initial-state" type="application/json">{"meta":{"access_token":"TOK"}}</script>'
    sent = {}

    def fake_post(self, url, **kw):
        sent.update(url=url, auth=self.headers["Authorization"], json=kw["json"])
        return httpx.Response(200, json={"id": "1", "url": "https://mastodon.example/@me/1"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    res = make("mastodon_web", {**a.creds, "_account_id": a.id}).publish_social(SocialPost(text="hi", link_url="https://e.com/"))
    assert res.url == "https://mastodon.example/@me/1"
    assert sent["auth"] == "Bearer TOK" and sent["url"] == "https://mastodon.example/api/v1/statuses"
