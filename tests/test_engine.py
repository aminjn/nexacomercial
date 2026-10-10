import datetime as dt

import httpx

from app import engine, importer
from app import models as m
from app.content import generate_article, generate_social
from app.llm import FakeLLM
from app.publishers.base import Publisher


def _mock(handler):
    def http(self, **kw):
        kw.pop("timeout", None)
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)
    return http


def add_site(**kw) -> m.Site:
    site = m.Site(name=kw.pop("name", "سایت تست"), url=kw.pop("url", "https://example.com"),
                  keywords=["کلمه اول"], pages=[{"url": "https://example.com/page"}], **kw)
    with m.session() as s:
        s.add(site)
        s.commit()
        s.refresh(site)
    return site


def add_campaign(site: m.Site, **kw) -> m.Campaign:
    c = m.Campaign(name="c", site_id=site.id, active_hours_start=0, active_hours_end=0, daily_limit=50, **kw)
    with m.session() as s:
        s.add(c)
        s.commit()
        s.refresh(c)
    return c


ACCOUNTS_CSV = """label,kind,tag,min_hours_between,daily_limit,bot_token,chat_id,url,author_name
tg-1,telegram,shop,0,5,123:abc,@one,,
tg-2,telegram,shop,0,5,456:def,@two,,
hook,webhook,other,0,5,,,https://hook.test/x,
tp,telegraph,shop,0,5,,,,Writer
bad,telegram,shop,,,,,,
nope,myspace,,,,,,,
"""


def test_import_accounts_encrypts_and_validates():
    n, errors = importer.import_accounts(ACCOUNTS_CSV)
    assert n == 4 and len(errors) == 2
    assert "missing bot_token" in errors[0] and "unknown kind" in errors[1]
    with m.session() as s:
        accs = s.exec(m.select(m.Account)).all()
    tg = next(a for a in accs if a.label == "tg-1")
    assert tg.creds == {"bot_token": "123:abc", "chat_id": "@one"}
    assert "123:abc" not in tg.creds_enc  # stored encrypted
    assert tg.category == "social" and next(a for a in accs if a.kind == "telegraph").category == "article"


def test_import_json_and_sites():
    n, errors = importer.import_accounts('[{"label":"b","kind":"bluesky","creds":{"handle":"me","app_password":"p"}}]',
                                         default_tag="g")
    assert (n, errors) == (1, [])
    n, errors = importer.import_sites("name,url,keywords,pages\nShop,https://s.com,a|b,https://s.com/x|https://s.com/y\n")
    assert (n, errors) == (1, [])
    with m.session() as s:
        site = s.exec(m.select(m.Site)).one()
        acc = s.exec(m.select(m.Account)).one()
    assert site.keywords == ["a", "b"] and len(site.pages) == 2 and acc.tag == "g"


def test_window_rules():
    c = m.Campaign(name="x", site_id=1, active_hours_start=22, active_hours_end=6, days_of_week=list(range(7)))
    tz = engine.tz()
    at = lambda h: dt.datetime(2026, 10, 5, h, tzinfo=tz).astimezone(dt.timezone.utc).replace(tzinfo=None)  # noqa: E731
    assert engine.in_window(c, at(23)) and engine.in_window(c, at(3)) and not engine.in_window(c, at(12))
    c.active_hours_start, c.active_hours_end = 0, 0
    c.days_of_week = [0]  # 2026-10-05 is a Monday
    assert engine.in_window(c, at(12))
    c.days_of_week = [1]
    assert not engine.in_window(c, at(12))
    c.days_of_week, c.end_date = [], dt.date(2026, 10, 1)
    assert not engine.in_window(c, at(12))


def test_next_run_respects_interval():
    c = m.Campaign(name="x", site_id=1, interval_min_minutes=30, interval_max_minutes=90)
    now = m.utcnow()
    for _ in range(50):
        d = (engine.next_run(c, now) - now).total_seconds() / 60
        assert 30 <= d <= 90


def test_campaign_rotates_accounts_and_respects_cooldown():
    importer.import_accounts(ACCOUNTS_CSV.replace("tg-1,telegram,shop,0", "tg-1,telegram,shop,24")
                             .replace("tg-2,telegram,shop,0", "tg-2,telegram,shop,24"))
    site = add_site()
    c = add_campaign(site, content_mode="social", account_tag="shop")
    with m.session() as s:
        for a in s.exec(m.select(m.Account)).all():
            a.last_used_at = None
        s.commit()
    used = []
    for _ in range(3):
        rec = engine.run_campaign(c.id, dry_run=True)
        if rec:
            used.append(rec.account_label)
            # dry runs don't touch last_used_at; simulate a real publish
            with m.session() as s:
                a = s.get(m.Account, rec.account_id)
                a.last_used_at = m.utcnow()
                s.add(a)
                s.commit()
    assert sorted(used) == ["tg-1", "tg-2"]  # 3rd run: both accounts in 24h cooldown
    with m.session() as s:
        assert s.get(m.Campaign, c.id).next_run_at > m.utcnow()


def test_article_published_and_account_stats(monkeypatch):
    def handler(req: httpx.Request):
        if req.url.path == "/createAccount":
            return httpx.Response(200, json={"ok": True, "result": {"access_token": "tok"}})
        return httpx.Response(200, json={"ok": True, "result": {"url": "https://telegra.ph/x-1", "path": "x-1"}})
    monkeypatch.setattr(Publisher, "http", _mock(handler))
    importer.import_accounts(ACCOUNTS_CSV)
    site = add_site()
    c = add_campaign(site, content_mode="article")
    rec = engine.run_campaign(c.id, dry_run=False)
    assert rec.status == "ok" and rec.url == "https://telegra.ph/x-1" and rec.category == "article"
    with m.session() as s:
        acc = s.get(m.Account, rec.account_id)
    assert acc.published_count == 1 and acc.last_used_at is not None


def test_failing_account_gets_paused(monkeypatch):
    monkeypatch.setattr(Publisher, "http", _mock(lambda req: httpx.Response(401, text="bad token")))
    importer.import_accounts("label,kind,min_hours_between,bot_token,chat_id\nt,telegram,0,1:x,@c\n")
    site = add_site()
    c = add_campaign(site, content_mode="social")
    for _ in range(3):
        rec = engine.run_campaign(c.id, dry_run=False)
        assert rec.status == "error" and "401" in rec.error
    with m.session() as s:
        acc = s.exec(m.select(m.Account)).one()
    assert acc.status == "paused" and not acc.usable
    assert engine.run_campaign(c.id, dry_run=False) is None  # no usable account left


def test_daily_limit_and_due_campaigns():
    importer.import_accounts(ACCOUNTS_CSV)
    site = add_site()
    c = add_campaign(site, content_mode="social")
    with m.session() as s:
        cc = s.get(m.Campaign, c.id)
        cc.daily_limit = 1
        s.add(cc)
        s.commit()
    assert engine.due_campaigns() == [c.id]
    assert engine.run_campaign(c.id, dry_run=True) is not None
    assert engine.run_campaign(c.id, dry_run=True) is None  # limit reached
    assert engine.due_campaigns() == []  # rescheduled into the future


def test_content_generation():
    site = m.Site(id=1, name="Brand", url="https://ex.com", keywords=["kw"], pages=[{"url": "https://ex.com/p"}])
    art = generate_article(FakeLLM(), site, [], "extra")
    assert art.body_markdown.count(f"]({art.link_url})") == 1
    post = generate_social(FakeLLM(), site, "x", max_chars=50)
    assert post.link_url in post.render(80) and len(post.render(80)) <= 80


def test_dashboard_and_forms():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        assert client.get("/health").json()["ok"] is True
        r = client.post("/sites", data={"name": "S", "url": "https://s.com", "keywords": "a, b", "enabled": "1"})
        assert r.status_code == 200
        r = client.post("/accounts", data={"kind": "telegram", "label": "t", "cred_bot_token": "1:x",
                                           "cred_chat_id": "@c", "min_hours_between": "0", "daily_limit": "5"})
        assert "ذخیره شد" in r.text and "1:x" not in r.text  # secret masked in the list
        r = client.post("/campaigns", data={"name": "C", "site_id": "1", "content_mode": "social", "enabled": "1",
                                            "interval_min_minutes": "60", "interval_max_minutes": "30",
                                            "active_hours_start": "0", "active_hours_end": "0",
                                            "days_of_week": ["0", "1"], "daily_limit": "5"})
        assert r.status_code == 200
        camp = client.get("/api/campaigns").json()[0]
        assert (camp["interval_min_minutes"], camp["interval_max_minutes"], camp["days_of_week"]) == (30, 60, [0, 1])
        r = client.post("/campaigns/1/preview")
        assert "dry_run" in r.text
        for path in ("/", "/sites", "/accounts", "/campaigns", "/publications", "/campaigns?edit=1", "/sites?edit=1"):
            assert client.get(path).status_code == 200, path
        assert client.get("/api/accounts").json()[0]["creds"]["bot_token"] == "•••"
        r = client.post("/api/accounts/import?tag=z", content="label,kind,handle,app_password\nb,bluesky,me,pw\n")
        assert r.json() == {"imported": 1, "errors": []}


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_site_image_upload_replace_and_remove():
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    with TestClient(app) as client:
        form = {"name": "S", "url": "https://s.com", "enabled": "1"}
        r = client.post("/sites", data=form, files={"image_file": ("a.png", PNG, "image/png")})
        assert "ذخیره شد" in r.text
        url = client.get("/api/sites").json()[0]["image_url"]
        assert url.startswith("http://testserver/media/") and url.endswith(".png")
        name = url.rsplit("/", 1)[1]
        assert client.get(f"/media/{name}").content == PNG  # publicly served
        # edit without a new file keeps the image
        client.post("/sites", data={**form, "id": "1"}, files={"image_file": ("", b"", "application/octet-stream")})
        assert client.get("/api/sites").json()[0]["image_url"] == url
        # non-image is rejected
        r = client.post("/sites", data={**form, "id": "1"}, files={"image_file": ("x.png", b"not an image", "image/png")})
        assert "فقط تصویر" in r.text
        # remove deletes the file
        client.post("/sites", data={**form, "id": "1", "remove_image": "1"})
        assert client.get("/api/sites").json()[0]["image_url"] == ""
        assert not (settings.uploads_path / name).exists()


def test_account_edit_keeps_secrets():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        client.post("/accounts", data={"kind": "telegram", "label": "t", "cred_bot_token": "1:x", "cred_chat_id": "@c"})
        page = client.get("/accounts?edit=1").text
        assert 'value="t"' in page and '"chat_id": "@c"' in page and "1:x" not in page
        client.post("/accounts", data={"id": "1", "kind": "telegram", "label": "t2", "cred_bot_token": "",
                                       "cred_chat_id": "@d", "min_hours_between": "6", "daily_limit": "2"})
        with m.session() as s:
            a = s.get(m.Account, 1)
        assert (a.label, a.creds, a.min_hours_between) == ("t2", {"bot_token": "1:x", "chat_id": "@d"}, 6.0)
        assert "شناسه‌ی اکانت" not in client.get("/accounts").text


def test_ai_failure_does_not_pause_account(monkeypatch):
    import app.engine as eng

    class Broken:
        def complete(self, *a, **k):
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
    monkeypatch.setattr(eng, "get_llm", lambda: Broken())
    importer.import_accounts("label,kind,min_hours_between,bot_token,chat_id\nt,telegram,0,1:x,@c\n")
    c = add_campaign(add_site(), content_mode="social")
    for _ in range(4):
        rec = engine.run_campaign(c.id, dry_run=False)
        assert rec.status == "error" and "[هوش مصنوعی" in rec.error and "DNS" in rec.error
    with m.session() as s:
        acc = s.exec(m.select(m.Account)).one()
    assert acc.usable and acc.fail_count == 0


def test_publish_error_is_explained(monkeypatch):
    monkeypatch.setattr(Publisher, "http", _mock(lambda req: httpx.Response(401, text="bad token")))
    importer.import_accounts("label,kind,min_hours_between,bot_token,chat_id\nt,telegram,0,1:x,@c\n")
    c = add_campaign(add_site(), content_mode="social")
    rec = engine.run_campaign(c.id, dry_run=False)
    assert "[انتشار در telegram]" in rec.error and "توکن" in rec.error


def test_llm_test_button():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        r = client.post("/llm-test")
        assert "هوش مصنوعی: وصل است" in r.text  # fake provider in tests


def test_settings_page_saves_and_applies(monkeypatch):
    from fastapi.testclient import TestClient

    from app import runtime
    from app.config import settings
    from app.main import app
    with TestClient(app) as client:
        assert client.get("/settings").status_code == 200
        r = client.post("/settings", data={"llm_provider": "ollama", "llm_base_url": "http://9.9.9.9:11434",
                                           "llm_api_key": "sekret", "llm_model": "qwen2.5:14b",
                                           "llm_timeout_sec": "300", "publish_proxy": "http://p:3128"})
        assert "ذخیره شد" in r.text
        assert (settings.llm_provider, settings.llm_base_url, settings.llm_api_key, settings.dry_run) == \
            ("ollama", "http://9.9.9.9:11434", "sekret", False)
        with m.session() as s:
            assert "sekret" not in s.get(runtime.AppSetting, "llm_api_key").value  # encrypted at rest
        # empty secret keeps the stored one; values survive a reload
        client.post("/settings", data={"llm_provider": "ollama", "llm_base_url": "http://9.9.9.9:11434",
                                       "llm_model": "m2", "dry_run": "1"})
        settings.llm_api_key = ""
        runtime.apply()
        assert (settings.llm_api_key, settings.llm_model, settings.dry_run, settings.publish_proxy) == \
            ("sekret", "m2", True, "http://p:3128")
        monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
            200, json={"models": [{"name": "qwen2.5:14b"}]}, request=httpx.Request("GET", url)))
        assert client.get("/settings/models", params={"provider": "ollama", "base_url": "http://x:11434"}).json() \
            == {"models": ["qwen2.5:14b"]}
        client.post("/settings", data={"llm_provider": "fake", "clear_llm_api_key": "1", "clear_publish_proxy": "1"})
        assert settings.llm_api_key == "" and settings.publish_proxy == ""


def test_account_form_hides_token_kinds_with_web_version():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        page = client.get("/accounts").text
        assert 'value="instagram_web"' in page and 'value="instagram"' not in page
        assert 'value="telegram"' in page and 'value="blogger"' in page
        a = m.Account(label="old", kind="instagram", category="social")
        with m.session() as s:
            s.add(a)
            s.commit()
        assert 'value="instagram"' in client.get("/accounts").text  # still editable when in use


def test_ready_posts_rotate_with_own_caption_and_image():
    from fastapi.testclient import TestClient

    from app.main import app
    site = add_site()
    acc = m.Account(label="ig", kind="instagram_web", category="social")
    with m.session() as s:
        s.add(acc)
        s.commit()
        s.refresh(acc)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    with TestClient(app) as client:
        assert client.get("/posts").status_code == 200
        r = client.post("/posts", data={"site_id": site.id, "caption": "کپشن خودم #ملکجت",
                                        "link_url": "https://example.com/rent", "enabled": "1"},
                        files={"image_file": ("a.png", png, "image/png")})
        assert "ذخیره شد" in r.text
        client.post("/posts", data={"site_id": site.id, "enabled": "1"}, files={"image_file": ("b.png", png, "image/png")})
        assert "هوش مصنوعی می‌نویسد" in client.get(f"/posts?site={site.id}").text
    with m.session() as s:
        own, ai = sorted(s.exec(m.select(m.MediaPost)).all(), key=lambda p: p.id)
    first = engine.publish(site, acc, dry_run=True)
    second = engine.publish(site, acc, dry_run=True)
    assert first.status == second.status == "dry_run"
    assert "کپشن خودم #ملکجت" in first.body_preview and first.link_url.startswith("https://example.com/rent?utm_source=instagram")
    assert "کپشن خودم" not in second.body_preview  # second ready post: AI text, its own image
    with m.session() as s:
        assert [p.used_count for p in sorted(s.exec(m.select(m.MediaPost)).all(), key=lambda p: p.id)] == [1, 1]


def test_instagram_and_homepage_urls_are_not_link_checked(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "check_backlink", lambda url, link: calls.append(url) or (True, ""))
    with m.session() as s:
        for kind, url in (("instagram_web", "https://www.instagram.com/"), ("linkedin_web", "https://www.linkedin.com/"),
                          ("telegraph", "https://telegra.ph/my-post-01")):
            s.add(m.Publication(site_id=1, account_id=1, account_kind=kind, url=url, link_url="https://example.com"))
        s.commit()
    assert engine.verify_links(max_age_hours=0) == 1 and calls == ["https://telegra.ph/my-post-01"]


def test_resend_publication(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    site = add_site()
    acc = m.Account(label="tg", kind="telegram", category="social")
    acc.creds = {"bot_token": "1:a", "chat_id": "@c"}
    with m.session() as s:
        s.add(acc)
        s.commit()
        s.refresh(acc)
    monkeypatch.setattr(Publisher, "http", _mock(lambda req: httpx.Response(
        200, json={"ok": True, "result": {"message_id": 7, "chat": {"username": "c"}}})))
    first = engine.publish(site, acc, dry_run=False)
    with TestClient(app) as client:
        assert "ارسال دوباره" in client.get("/publications").text
        r = client.post(f"/publications/{first.id}/resend", headers={"referer": "http://t/publications"})
        assert "ارسال دوباره: ok" in r.text
    with m.session() as s:
        old = s.get(m.Publication, first.id)
        assert old.status == "error" and "دوباره ارسال شد" in old.error
        assert len(s.exec(m.select(m.Publication)).all()) == 2


def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    import sqlite3

    from app.config import settings
    db = tmp_path / "old"
    db.mkdir()
    con = sqlite3.connect(db / "nexa.db")
    con.execute("CREATE TABLE publication (id INTEGER PRIMARY KEY, created_at DATETIME, site_id INTEGER, "
                "account_id INTEGER, title VARCHAR, status VARCHAR)")
    con.execute("INSERT INTO publication (created_at, site_id, account_id, title, status) "
                "VALUES ('2026-10-01 10:00:00', 1, 1, 'old post', 'ok')")
    con.commit()
    con.close()
    monkeypatch.setattr(settings, "data_dir", str(db))
    m.reset_engine()
    try:
        with m.session() as s:
            p = s.exec(m.select(m.Publication)).one()
            assert p.title == "old post" and p.likes is None and p.image_url == "" and p.body_preview == ""
    finally:
        m.reset_engine()


def test_site_page_tabs_and_stats_parsing():
    from fastapi.testclient import TestClient

    from app import stats
    from app.main import app
    assert stats.parse_meta("1,234 likes, 56 comments - melkjet on October 10, 2026") == (1234, 56)
    assert stats.parse_meta("2.5K likes, 3 comments - x") == (2500, 3)
    assert stats.parse_meta("no numbers here") is None
    site = add_site()
    with m.session() as s:
        s.add(m.Publication(site_id=site.id, account_id=1, account_kind="instagram_web", status="ok", category="social",
                            url="https://www.instagram.com/p/ABC/", image_url="https://x/media/a.jpg",
                            body_preview="کپشن تست", likes=12, comments=3))
        s.add(m.Publication(site_id=site.id, account_id=2, account_kind="telegraph", status="ok",
                            url="https://telegra.ph/a-01", link_found=True))
        s.commit()
    with TestClient(app) as client:
        r = client.get(f"/sites/{site.id}")
        assert r.status_code == 200 and "به تفکیک پلتفرم" in r.text and "12 / 3" in r.text
        r = client.get(f"/sites/{site.id}?tab=instagram")
        assert "❤ 12" in r.text and "💬 3" in r.text and "کپشن تست" in r.text
        assert "اینستاگرام (1)" in r.text and "Telegraph (1)" in r.text  # one tab per platform
        assert "بک‌لینک زنده" in client.get(f"/sites/{site.id}?tab=telegraph").text
        assert "پست جدید برای این سایت" in client.get(f"/sites/{site.id}?tab=posts").text
        assert f'href="/sites/{site.id}"' in client.get("/sites").text


def test_public_stats_for_each_platform(monkeypatch):
    from app import stats
    pages = {
        "https://t.me/chan/42": '<span class="tgme_widget_message_views">1.5K</span>',
        "https://api.telegra.ph/getViews/a-01": {"ok": True, "result": {"views": 33}},
        "https://dev.to/api/articles/me/post-1": {"public_reactions_count": 4, "comments_count": 2},
        "https://mstdn.social/api/v1/statuses/111": {"favourites_count": 5, "replies_count": 1, "reblogs_count": 2},
        "https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts": {"posts": [{"likeCount": 9, "replyCount": 0, "repostCount": 1}]},
        "https://old.reddit.com/r/test/comments/abc/t.json": [{"data": {"children": [{"data": {"score": 17, "num_comments": 6}}]}}],
        "https://write.as/api/posts/w1": {"data": {"views": 8}},
        "https://blog.test/wp-json/wp/v2/comments": ("hdr", 3),
        "https://my.blogspot.com/feeds/77/comments/default": {"feed": {"openSearch$totalResults": {"$t": "4"}}},
    }

    def handler(req):
        url = str(req.url).split("?")[0] if "embed" not in str(req.url) else str(req.url).split("?")[0]
        body = pages[url]
        if isinstance(body, tuple):
            return httpx.Response(200, json=[], headers={"X-WP-Total": str(body[1])})
        return httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)
    monkeypatch.setattr(stats, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    rows = [("telegram", "https://t.me/chan/42", "42"), ("telegraph", "https://telegra.ph/a-01", "a-01"),
            ("devto_web", "https://dev.to/me/post-1", ""), ("mastodon_web", "https://mstdn.social/@me/111", ""),
            ("bluesky", "https://bsky.app/profile/me/post/k", "at://did:plc:x/app.bsky.feed.post/k"),
            ("reddit_web", "https://old.reddit.com/r/test/comments/abc/t/", ""), ("writeas", "https://write.as/b/t", "w1"),
            ("wordpress", "https://blog.test/hello", "5"), ("blogger", "https://my.blogspot.com/2026/10/p.html", "77"),
            ("linkedin_web", "https://www.linkedin.com/in/me/", "")]
    with m.session() as s:
        for kind, url, ext in rows:
            s.add(m.Publication(site_id=1, account_id=1, account_kind=kind, status="ok", url=url, external_id=ext))
        s.commit()
    assert stats.refresh_public(max_age_hours=0) == 9
    with m.session() as s:
        got = {p.account_kind: (p.views, p.likes, p.comments, p.shares) for p in s.exec(m.select(m.Publication)).all()}
    assert got["telegram"] == (1500, None, None, None) and got["telegraph"][0] == 33
    assert got["devto_web"][1:3] == (4, 2) and got["mastodon_web"][1:] == (5, 1, 2) and got["bluesky"][1:] == (9, 0, 1)
    assert got["reddit_web"][1:3] == (17, 6) and got["writeas"][0] == 8
    assert got["wordpress"][2] == 3 and got["blogger"][2] == 4 and got["linkedin_web"] == (None, None, None, None)


def test_site_pages_with_keywords_and_merge():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        client.post("/sites", data={"name": "ملکجت", "url": "https://melkjet.com/", "keywords": "ملک، اجاره", "enabled": "1",
                                    "pages": "https://melkjet.com/search | جستجوی هوشمند، جستجوی ملک\nhttps://melkjet.com/about"})
        client.post("/sites", data={"name": "ملکجت / اجاره", "url": "https://melkjet.com/search?type=rent",
                                    "keywords": "اجاره آپارتمان", "enabled": "1"})
        with m.session() as s:
            main, rent = sorted(s.exec(m.select(m.Site)).all(), key=lambda x: x.id)
            assert main.pages == [{"url": "https://melkjet.com/search", "keywords": ["جستجوی هوشمند", "جستجوی ملک"]},
                                  {"url": "https://melkjet.com/about"}]
            s.add(m.Publication(site_id=rent.id, account_id=1, status="ok", category="social", clicks=4,
                                link_url="https://melkjet.com/search?type=rent&utm_source=telegram&utm_medium=social"))
            s.add(m.MediaPost(site_id=rent.id, image_url="https://x/media/a.jpg"))
            s.commit()
        import json as _json
        html = client.get(f"/sites?edit={main.id}").text
        pages = _json.loads(html.split("const PAGES = ", 1)[1].split(";\n", 1)[0])  # what the page's JS receives
        assert pages[0] == {"url": "https://melkjet.com/search", "keywords": ["جستجوی هوشمند", "جستجوی ملک"]}
        r = client.post(f"/sites/{rent.id}/merge", data={"target_id": main.id})
        assert "ادغام شد" in r.text
        page = client.get(f"/sites/{main.id}").text
        assert "melkjet.com/search?type=rent" in page and "به تفکیک صفحه" in page
    with m.session() as s:
        sites = s.exec(m.select(m.Site)).all()
        assert len(sites) == 1 and {"url": "https://melkjet.com/search?type=rent", "keywords": ["اجاره آپارتمان"]} in sites[0].pages
        assert "اجاره آپارتمان" in sites[0].keywords
        assert s.exec(m.select(m.Publication)).one().site_id == main.id
        assert s.exec(m.select(m.MediaPost)).one().site_id == main.id
