import json

import httpx

from app import engine, ga, tracking
from app import models as m
from app.config import settings
from app.publishers.base import Publisher
from tests.test_engine import _mock, add_campaign, add_site


def _telegram_account() -> m.Account:
    acc = m.Account(label="tg", kind="telegram", category="social")
    acc.creds = {"bot_token": "1:a", "chat_id": "@c"}
    with m.session() as s:
        s.add(acc)
        s.commit()
        s.refresh(acc)
    return acc


def test_social_post_gets_utm_and_counting_short_link(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(settings, "public_url", "https://cm.example.ir")
    sent_text = []

    def handler(req):
        sent_text.append(json.loads(req.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5, "chat": {"username": "c"}}})
    monkeypatch.setattr(Publisher, "http", _mock(handler))
    ga_events = []
    monkeypatch.setattr(ga, "send_click", lambda params: ga_events.append(params))
    site = add_site()
    camp = add_campaign(site)
    with m.session() as s:
        camp = s.get(m.Campaign, camp.id)
        camp.name = "کمپین پاییز"
        s.add(camp)
        s.commit()
    rec = engine.publish(site, _telegram_account(), campaign=camp, dry_run=False)
    assert rec.status == "ok" and rec.track_code
    assert f"https://cm.example.ir/r/{rec.track_code}" in sent_text[0]  # the post carries the short link
    assert "utm_source=telegram" in rec.link_url and "utm_medium=social" in rec.link_url
    assert "utm_campaign=%DA%A9" in rec.link_url and f"utm_content={rec.track_code}" in rec.link_url
    with TestClient(app) as client:
        r = client.get(f"/r/{rec.track_code}", headers={"user-agent": "TelegramBot (like TwitterBot)"},
                       follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == rec.link_url
        r = client.get(f"/r/{rec.track_code}", headers={"user-agent": "Mozilla/5.0 (iPhone)"}, follow_redirects=False)
        assert r.status_code == 302
        assert client.get("/r/nope", follow_redirects=False).status_code == 404
        page = client.get("/reports").text
        assert "کمپین پاییز" in page and "تلگرام: 1 / 1" in page
    with m.session() as s:
        assert s.get(m.Publication, rec.id).clicks == 1  # the bot preview did not count
        assert len(s.exec(m.select(m.Click)).all()) == 1
    assert ga_events == [{"campaign": "کمپین-پاییز", "source": "telegram", "medium": "social",
                          "content": rec.track_code, "publication_id": rec.id}]


def test_article_backlink_is_tagged_but_not_shortened(monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://cm.example.ir")
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {"url": "https://telegra.ph/x-01", "path": "x-01"}})
    monkeypatch.setattr(Publisher, "http", _mock(handler))
    acc = m.Account(label="tp", kind="telegraph", category="article")
    acc.creds = {"access_token": "t"}
    with m.session() as s:
        s.add(acc)
        s.commit()
        s.refresh(acc)
    rec = engine.publish(add_site(), acc, dry_run=False)
    assert rec.status == "ok" and "utm_medium=article" in rec.link_url and "/r/" not in json.dumps(bodies)
    assert rec.link_url.split("?")[0] in ("https://example.com", "https://example.com/page")
    assert "utm_source=telegraph" in json.dumps(bodies[-1], ensure_ascii=False)


def test_ga_goal_and_report(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient

    from app.main import app
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    sa = json.dumps({"type": "service_account", "client_email": "nexa@proj.iam.gserviceaccount.com", "private_key": key})
    calls = []

    def handler(req):
        calls.append((req.method, str(req.url)))
        if "oauth2" in str(req.url):
            return httpx.Response(200, json={"access_token": "AT"})
        assert req.headers["Authorization"] == "Bearer AT"
        if req.url.path.endswith("/keyEvents"):
            assert json.loads(req.content)["eventName"] == "nexa_click"
            return httpx.Response(201, json={})
        return httpx.Response(200, json={"rows": [{"dimensionValues": [{"value": "کمپین-پاییز"}, {"value": "telegram"},
                                                                     {"value": "social"}],
                                                   "metricValues": [{"value": "12"}, {"value": "9"}, {"value": "3"}]}]})
    monkeypatch.setattr(ga, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    ga._token.update(value="", exp=0, key="")
    with TestClient(app) as client:
        r = client.post("/settings", data={"llm_provider": "fake", "utm_enabled": "1", "click_redirect": "1",
                                           "ga_property_id": "properties/123", "ga_service_account": sa})
        assert "هدف «nexa_click» در گوگل آنالیتیکس ساخته شد" in r.text
        assert settings.ga_property_id == "123"
        assert "nexa@proj.iam.gserviceaccount.com" in client.get("/settings").text
        page = client.get("/reports").text
        assert "کمپین-پاییز" in page and "<td>12</td><td>9</td><td>3</td>" in page
        r = client.post("/settings", data={"llm_provider": "fake", "ga_service_account": "not json"})
        assert "نامعتبر" in r.text
    assert any(u.endswith("/properties/123/keyEvents") for _, u in calls)
    assert any(":runReport" in u for _, u in calls)


def test_slug_and_tag_keep_existing_params():
    assert tracking.slug("  Melkjet Autumn / Sale ") == "melkjet-autumn-sale"
    assert tracking.tag("https://a.com/p?utm_source=x&q=1", source="s", medium="m", campaign="c", content="k") == \
        "https://a.com/p?utm_source=x&q=1&utm_medium=m&utm_campaign=c&utm_content=k"
