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
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGES[key])
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


def test_instagram_recipe_persian_ui_and_error_shot(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.publishers.base import PublishError
    a = account("instagram_web")
    logged_in(a, ".instagram.com")
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    PAGES["https://www.instagram.com/"] = """<svg aria-label="پست جدید" width=20 height=20 onclick="document.getElementById('d').style.display='block'"><rect width=20 height=20 /></svg>
      <div role="dialog" id="d" style="display:none"><input type="file" onchange="document.getElementById('n').style.display='inline'">
      <button id="n" style="display:none" onclick="this.dataset.c=(+this.dataset.c||0)+1; if(this.dataset.c==2){document.getElementById('cap').style.display='block';document.getElementById('s').style.display='inline'}">بعدی</button>
      <div id="cap" aria-label="Write a caption..." contenteditable="true" style="display:none"></div>
      <button id="s" style="display:none" onclick="fetch('/share',{method:'POST',body:document.getElementById('cap').innerText});document.getElementById('cap').remove();document.body.insertAdjacentHTML('beforeend','<p>پست شما به اشتراک گذاشته شد.</p>')">اشتراک‌گذاری</button></div>"""
    PAGES["https://www.instagram.com/share"] = "ok"
    res = make("instagram_web", {"_account_id": a.id}).publish_social(
        SocialPost(text="سلام", link_url="https://example.com/", image_url=str(img)))
    assert res.url == "https://www.instagram.com/" and "سلام" in POSTED[0]["body"]
    PAGES["https://www.instagram.com/"] = "<h1>nothing here</h1>"
    PAGES["https://www.instagram.com/create/select/"] = "<h1>still nothing</h1>"
    with pytest.raises(PublishError):
        make("instagram_web", {"_account_id": a.id}).publish_social(
            SocialPost(text="x", link_url="https://example.com/", image_url=str(img)))
    with TestClient(app) as client:
        assert "عکس صفحه در لحظه‌ی آخرین خطا" in client.get("/accounts").text
        assert client.get(f"/accounts/{a.id}/error-shot").content[:2] == b"\xff\xd8"


def test_square_image_pads_wide_logo(tmp_path):
    from app.publishers.web import WebPublisher

    def jpeg_size(b: bytes) -> tuple[int, int]:
        i = 2
        while i < len(b):
            marker, length = b[i + 1], int.from_bytes(b[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xC2:
                return int.from_bytes(b[i + 7:i + 9], "big"), int.from_bytes(b[i + 5:i + 7], "big")
            i += 2 + length
        raise ValueError("no SOF")

    def job():
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            png = page.evaluate("""() => { const c = document.createElement('canvas'); c.width = 1200; c.height = 350;
                return c.toDataURL('image/png').split(',')[1]; }""")  # fully transparent wide image
            src = tmp_path / "logo.png"
            import base64
            src.write_bytes(base64.b64decode(png))
            out = WebPublisher.square_image(page, str(src))
            return open(out, "rb").read()
        finally:
            ctx.close()
    data = browser.call(job)
    assert data[:2] == b"\xff\xd8"
    w, h = jpeg_size(data)
    assert w == h and w > 1200


def test_instagram_reports_failure_instead_of_success():
    from app.publishers.base import PublishError
    a = account("instagram_web", handle="melkjet")
    logged_in(a, ".instagram.com")
    PAGES["https://www.instagram.com/"] = """<div role="dialog"><input type="file" onchange="document.getElementById('n').style.display='inline'">
      <button id="n" style="display:none" onclick="document.getElementById('cap').style.display='block';document.getElementById('s').style.display='inline'">Next</button>
      <div id="cap" aria-label="Write a caption..." contenteditable="true" style="display:none"></div>
      <button id="s" style="display:none" onclick="document.getElementById('cap').remove();document.body.insertAdjacentHTML('beforeend','<p>Your post couldn\\'t be shared.</p>')">Share</button></div>"""
    PAGES["https://www.instagram.com/create/select/"] = PAGES["https://www.instagram.com/"]
    import base64
    img = browser.settings.data_path / "t.png"
    img.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="))
    with pytest.raises(PublishError, match="منتشر نکرد"):
        make("instagram_web", {**a.creds, "_account_id": a.id}).publish_social(
            SocialPost(text="x", link_url="https://example.com/", image_url=str(img)))


def test_instagram_stats_refresh():
    from app import stats
    a = account("instagram_web", handle="melkjet")
    logged_in(a, ".instagram.com")
    with m.session() as s:
        s.add(m.Publication(site_id=1, account_id=a.id, account_kind="instagram_web", status="ok",
                            url="https://www.instagram.com/p/ABC/"))
        s.add(m.Publication(site_id=1, account_id=a.id, account_kind="instagram_web", status="ok",
                            url="https://www.instagram.com/"))  # no post address: skipped
        s.commit()
    PAGES["https://www.instagram.com/p/ABC/"] = '<meta name="description" content="7 likes, 2 comments - melkjet on October 10, 2026">'
    assert stats.refresh_instagram(max_age_hours=0) == 1
    with m.session() as s:
        p = s.exec(m.select(m.Publication).where(m.Publication.url == "https://www.instagram.com/p/ABC/")).one()
        assert (p.likes, p.comments) == (7, 2) and p.stats_at is not None


def test_site_form_tags_and_page_rows_in_a_real_browser():
    """Type keywords as tags and add page rows like a user would; the form must send the old formats."""
    from fastapi.testclient import TestClient

    from app.main import app
    with m.session() as s:
        site = m.Site(name="ملکجت", url="https://melkjet.com/", keywords=["ملک"],
                      pages=[{"url": "https://melkjet.com/search", "keywords": ["جستجوی ملک"]}])
        s.add(site)
        s.commit()
        s.refresh(site)
    with TestClient(app) as client:
        html = client.get(f"/sites?edit={site.id}").text

    def job():
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            page.set_content(html)
            kw = page.locator('.chips[data-name="keywords"] input:not([type=hidden])')
            kw.fill("اجاره")
            kw.press("Enter")
            kw.fill("خرید و فروش")
            kw.press("Enter")
            page.locator('.chips[data-name="anchors"] input:not([type=hidden])').fill("املاک تهران")
            page.locator("#add_page").click()
            row = page.locator(".page-row").nth(1)
            row.locator("input.ltr").fill("https://melkjet.com/search?type=rent")
            tag = row.locator(".chips input:not([type=hidden])").nth(0)
            tag.fill("اجاره آپارتمان، رهن")  # a pasted list with Persian commas also works
            tag.press("Enter")
            anchor = row.locator(".chips input:not([type=hidden])").nth(1)  # this page's own anchor text
            anchor.fill("اجاره آپارتمان در تهران")
            anchor.press("Enter")
            page.locator(".page-row").nth(0).locator(".chip button").click()  # remove the first page's keyword
            page.locator('form[action="/sites"]').evaluate("f => f.dispatchEvent(new Event('submit'))")
            return {
                "keywords": page.locator('input[name="keywords"]').input_value(),
                "anchors": page.locator('input[name="anchors"]').input_value(),
                "pages": page.locator("#pages_field").input_value(),
            }
        finally:
            ctx.close()
    got = browser.call(job)
    assert got["keywords"] == "ملک, اجاره, خرید و فروش"
    assert got["anchors"] == "املاک تهران"
    assert got["pages"] == ("https://melkjet.com/search\n"
                            "https://melkjet.com/search?type=rent | اجاره آپارتمان, رهن | اجاره آپارتمان در تهران")


def test_import_cookies_from_own_browser(tmp_path, monkeypatch):
    import json

    from app import browser
    monkeypatch.setattr(browser, "_session_file", lambda aid: tmp_path / f"{aid}.bin")
    n = browser.import_cookies(9, json.dumps([
        {"name": "SID", "value": "abc", "domain": ".google.com", "path": "/", "expirationDate": 1999999999.5,
         "httpOnly": True, "secure": True, "sameSite": "no_restriction"},
        {"name": "x", "value": "1", "domain": "www.blogger.com", "sameSite": "lax"}]))
    assert n == 2
    st = browser.load_state(9)
    assert st["cookies"][0]["sameSite"] == "None" and st["cookies"][0]["expires"] == 1999999999.5
    assert st["cookies"][1]["expires"] == -1 and st["origins"] == []
    browser.import_cookies(9, ".google.com\tTRUE\t/\tTRUE\t0\tSID\tnew")
    sids = [c for c in browser.load_state(9)["cookies"] if c["name"] == "SID"]
    assert len(sids) == 1 and sids[0]["value"] == "new"
