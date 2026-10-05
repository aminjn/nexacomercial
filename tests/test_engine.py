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
