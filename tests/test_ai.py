import base64
import io

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app import ai, engine, importer
from app import models as m
from app.config import settings
from app.main import app


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(out, "PNG")
    return out.getvalue()


def conns():
    with m.session() as s:
        return s.exec(m.select(m.AIConnection).order_by(m.AIConnection.id)).all()


def test_add_many_connections_default_and_secret(monkeypatch):
    with TestClient(app) as client:
        r = client.post("/ai", data={"kind": "text", "provider": "ollama", "name": "اصلی",
                                     "base_url": "http://1.1.1.1:11434/", "model": "qwen2.5:14b", "api_key": "k1"})
        assert "ذخیره شد" in r.text
        client.post("/ai", data={"kind": "text", "provider": "openai", "name": "دوم",
                                 "base_url": "http://2.2.2.2:8000/v1", "model": "llama3"})
        client.post("/ai", data={"kind": "image", "provider": "a1111", "name": "GPU",
                                 "base_url": "http://3.3.3.3:7860", "width": "896", "height": "1152"})
        a, b, g = conns()
        assert a.is_default and not b.is_default and g.is_default  # first of each kind becomes default
        assert a.base_url == "http://1.1.1.1:11434" and a.api_key == "k1" and "k1" not in a.api_key_enc
        assert g.opt("width") == 896 and g.opt("height") == 1152
        # edit without key keeps it; make the second one default
        client.post("/ai", data={"id": a.id, "kind": "text", "provider": "ollama", "name": "اصلی",
                                 "base_url": "http://1.1.1.1:11434", "model": "qwen2.5:7b", "enabled": "1"})
        client.post(f"/ai/{b.id}/default")
        a, b, g = conns()
        assert a.api_key == "k1" and a.model == "qwen2.5:7b" and b.is_default and not a.is_default
        page = client.get("/settings").text
        assert "اصلی" in page and "دوم" in page and "GPU" in page and "k1" not in page
        assert client.get(f"/settings?edit={g.id}").status_code == 200
        assert client.get("/settings?new=image").status_code == 200
        # deleting the default promotes another one
        client.post(f"/ai/{b.id}/delete")
        assert conns()[0].is_default


def test_failover_to_next_connection(monkeypatch):
    with TestClient(app) as client:
        client.post("/ai", data={"kind": "text", "provider": "ollama", "name": "خراب",
                                 "base_url": "http://dead:11434", "model": "m"})
        client.post("/ai", data={"kind": "text", "provider": "fake", "name": "سالم"})
    calls = []

    def post(url, **kw):
        calls.append(url)
        raise httpx.ConnectError("[Errno 111] Connection refused")
    monkeypatch.setattr(httpx, "post", post)
    llm = ai.llm_for()
    out = llm.complete("s", "SOCIAL test")
    assert calls == ["http://dead:11434/api/chat"] and "text" in out and llm.used.name == "سالم"


def test_campaign_uses_chosen_connections(monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://cm.x.ir")
    with TestClient(app) as client:
        client.post("/ai", data={"kind": "text", "provider": "fake", "name": "پیش‌فرض"})
        client.post("/ai", data={"kind": "image", "provider": "a1111", "name": "img-1", "base_url": "http://g1:7860"})
        client.post("/ai", data={"kind": "image", "provider": "a1111", "name": "img-2", "base_url": "http://g2:7860"})
    img2 = conns()[2]
    hit = []

    def post(url, **kw):
        hit.append(url)
        return httpx.Response(200, json={"images": [base64.b64encode(png()).decode()]})
    monkeypatch.setattr(httpx, "post", post)
    importer.import_accounts("label,kind,min_hours_between,ig_user_id,access_token\nig,instagram,0,1,t\n")
    with m.session() as s:
        site = m.Site(name="S", url="https://s.com")
        s.add(site)
        s.commit()
        s.refresh(site)
        c = m.Campaign(name="c", site_id=site.id, active_hours_start=0, active_hours_end=0,
                       content_mode="social", image_ai_id=img2.id)
        s.add(c)
        s.commit()
        s.refresh(c)
    rec = engine.run_campaign(c.id, dry_run=True)
    assert rec.status == "dry_run", rec.error
    assert hit == ["http://g2:7860/sdapi/v1/txt2img"] and rec.image_url.endswith(".jpg")


def test_connection_test_button_records_result(monkeypatch):
    with TestClient(app) as client:
        client.post("/ai", data={"kind": "text", "provider": "fake", "name": "f"})
        client.post("/ai", data={"kind": "image", "provider": "a1111", "name": "g", "base_url": "http://g:7860"})
        f, g = conns()
        r = client.post(f"/ai/{f.id}/test")
        assert "وصل است" in r.text
        monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(
            200, json={"images": [base64.b64encode(png()).decode()]}))
        r = client.post(f"/ai/{g.id}/test")
        assert "تصویر ساخته شد" in r.text and '<img src="/media/test-' in r.text
        monkeypatch.setattr(httpx, "post", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("refused")))
        r = client.post(f"/ai/{g.id}/test")
        assert "[تولید تصویر]" in r.text
        f, g = conns()
        assert f.last_test_ok is True and g.last_test_ok is False
        assert "وصل" in client.get("/").text  # dashboard summary
        r = client.post("/ai", data={"kind": "image", "provider": "comfyui", "name": "c", "comfy_workflow": "{bad"})
        assert "JSON معتبر نیست" in r.text


def test_old_settings_become_connections(monkeypatch):
    from app import runtime
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_base_url", "http://9.9.9.9:11434")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:14b")
    monkeypatch.setattr(settings, "image_provider", "comfyui")
    monkeypatch.setattr(settings, "image_base_url", "http://9.9.9.9:8188")
    ai.seed_from_settings()
    t, i = conns()
    assert (t.kind, t.provider, t.base_url, t.model, t.is_default) == ("text", "ollama", "http://9.9.9.9:11434", "qwen2.5:14b", True)
    assert (i.kind, i.provider, i.base_url) == ("image", "comfyui", "http://9.9.9.9:8188")
    with m.session() as s:
        for c in s.exec(m.select(m.AIConnection)).all():
            s.delete(c)
        s.commit()
    ai.seed_from_settings()  # runs only once: deleting everything must not bring them back
    assert conns() == []
    assert runtime  # imported for table registration
