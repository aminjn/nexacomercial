import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app import engine, images, importer
from app import models as m
from app.config import settings
from app.llm import FakeLLM
from app.publishers.base import Publisher


def png_bytes(color=(200, 60, 60, 128)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (64, 48), color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def img_settings(monkeypatch):
    def set_(**kw):
        for k, v in {"image_base_url": "http://gpu:7860", "public_url": "https://cm.example.ir", **kw}.items():
            monkeypatch.setattr(settings, k, v)
    return set_


def test_a1111_request_and_jpeg(monkeypatch, img_settings):
    img_settings(image_provider="a1111", image_model="sdxl.safetensors", image_style="photo")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json)
        return httpx.Response(200, json={"images": [base64.b64encode(png_bytes()).decode()]})
    monkeypatch.setattr(httpx, "post", post)
    url, prompt = images.generate_for_post(FakeLLM(), "املاک", "مشاور املاک", "نکته‌ی خرید خانه")
    assert seen["url"] == "http://gpu:7860/sdapi/v1/txt2img"
    assert seen["body"]["prompt"] == prompt + ", photo"
    assert seen["body"]["override_settings"] == {"sd_model_checkpoint": "sdxl.safetensors"}
    assert url.startswith("https://cm.example.ir/media/gen-") and url.endswith(".jpg")
    data = (settings.uploads_path / url.rsplit("/", 1)[1]).read_bytes()
    assert data.startswith(b"\xff\xd8\xff")  # converted to JPEG for Instagram


def test_comfyui_placeholders_and_polling(monkeypatch, img_settings):
    wf = {"6": {"inputs": {"text": "{{prompt}}"}}, "3": {"inputs": {"seed": "{{seed}}", "steps": "{{steps}}"}},
          "5": {"inputs": {"width": "{{width}}", "height": "{{height}}"}}, "7": {"inputs": {"text": "{{negative}}"}}}
    img_settings(image_provider="comfyui", image_base_url="http://gpu:8188", image_comfy_workflow=json.dumps(wf),
                 image_steps=8, image_width=896, image_height=1152, image_negative_prompt="blurry")
    sent, polls = {}, []

    def post(url, json=None, headers=None, timeout=None):
        sent.update(json["prompt"])
        return httpx.Response(200, json={"prompt_id": "abc"})

    def get(url, headers=None, timeout=None, params=None):
        if "/history/" in url:
            polls.append(1)
            if len(polls) < 2:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"abc": {"outputs": {"9": {"images": [
                {"filename": "x.png", "subfolder": "", "type": "output"}]}}}})
        assert params["filename"] == "x.png"
        return httpx.Response(200, content=png_bytes(), request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(images.time, "sleep", lambda s: None)
    data = images.generate_bytes("a house")
    assert data.startswith(b"\x89PNG") and len(polls) == 2
    assert sent["6"]["inputs"]["text"].startswith("a house")
    assert sent["3"]["inputs"]["steps"] == 8 and isinstance(sent["3"]["inputs"]["seed"], int)
    assert sent["5"]["inputs"] == {"width": 896, "height": 1152} and sent["7"]["inputs"]["text"] == "blurry"


def test_openai_images(monkeypatch, img_settings):
    img_settings(image_provider="openai", image_base_url="http://gpu/v1", image_api_key="k", image_model="flux")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, headers=headers)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]})
    monkeypatch.setattr(httpx, "post", post)
    assert images.generate_bytes("x").startswith(b"\x89PNG")
    assert seen["url"] == "http://gpu/v1/images/generations" and seen["body"]["size"] == "1024x1024"
    assert seen["headers"] == {"Authorization": "Bearer k"} and seen["body"]["model"] == "flux"


def test_requires_public_url(img_settings):
    img_settings(image_provider="a1111", public_url="")
    with pytest.raises(images.ImageError, match="آدرس عمومی"):
        images.generate_for_post(FakeLLM(), "n", "d", "t")


def _instagram_setup():
    importer.import_accounts("label,kind,min_hours_between,ig_user_id,access_token\nig,instagram,0,123,tok\n")
    site = m.Site(name="ملک‌جت", url="https://melkjet.com", niche="املاک")
    with m.session() as s:
        s.add(site)
        s.commit()
        s.refresh(site)
    c = m.Campaign(name="c", site_id=site.id, active_hours_start=0, active_hours_end=0, content_mode="social")
    with m.session() as s:
        s.add(c)
        s.commit()
        s.refresh(c)
    return c


def test_instagram_post_gets_generated_jpeg(monkeypatch, img_settings):
    img_settings(image_provider="a1111")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(
        200, json={"images": [base64.b64encode(png_bytes()).decode()]}))
    from urllib.parse import parse_qsl
    posted, polls = {}, []

    def handler(req: httpx.Request):
        if req.url.path.endswith("/media"):
            posted.update(dict(parse_qsl(req.content.decode())))
            return httpx.Response(200, json={"id": "container1"})
        if req.url.path.endswith("/container1"):
            polls.append(1)
            return httpx.Response(200, json={"status_code": "IN_PROGRESS" if len(polls) < 2 else "FINISHED"})
        if req.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "post1"})
        return httpx.Response(200, json={"id": "post1", "permalink": "https://instagram.com/p/x"})

    def http(self, **kw):
        kw.pop("timeout", None)
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(Publisher, "http", http)
    import app.publishers.socials as soc
    monkeypatch.setattr(soc.time, "sleep", lambda s: None)
    c = _instagram_setup()
    rec = engine.run_campaign(c.id, dry_run=False)
    assert rec.status == "ok", rec.error
    assert posted["image_url"] == rec.image_url and len(polls) == 2
    assert rec.image_url.startswith("https://cm.example.ir/media/gen-") and rec.image_url.endswith(".jpg")
    assert "[image prompt]" in rec.body_preview


def test_image_failure_falls_back_to_site_image(monkeypatch, img_settings):
    img_settings(image_provider="a1111")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    c = _instagram_setup()
    rec = engine.run_campaign(c.id, dry_run=True)
    assert rec.status == "error" and "[تولید تصویر]" in rec.error  # no site image → explained error
    name = "site.png"
    (settings.uploads_path / name).write_bytes(png_bytes())
    with m.session() as s:
        site = s.get(m.Site, c.site_id)
        site.image_url = f"https://cm.example.ir/media/{name}"
        s.add(site)
        s.commit()
    rec = engine.run_campaign(c.id, dry_run=True)
    assert rec.status == "dry_run" and rec.image_url == "https://cm.example.ir/media/site.jpg"
    assert (settings.uploads_path / "site.jpg").read_bytes().startswith(b"\xff\xd8\xff")


def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE publication (id INTEGER PRIMARY KEY, site_id INTEGER, account_id INTEGER)")
    con.commit()
    con.close()
    from sqlmodel import create_engine
    eng = create_engine(f"sqlite:///{db}")
    m._add_missing_columns(eng)
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(publication)")}
    assert {"image_url", "body_preview", "status"} <= cols


def test_settings_image_test_button(monkeypatch, img_settings):
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        client.post("/settings", data={"llm_provider": "fake", "image_provider": "a1111",
                                       "image_base_url": "http://gpu:7860", "image_width": "896",
                                       "image_height": "1152", "public_url": "https://cm.example.ir"})
        assert (settings.image_provider, settings.image_width) == ("a1111", 896)
        monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(
            200, json={"images": [base64.b64encode(png_bytes()).decode()]}))
        r = client.post("/settings/image-test")
        assert "تصویر ساخته شد" in r.text and '<img src="/media/test-' in r.text
        r = client.post("/settings", data={"llm_provider": "fake", "image_provider": "comfyui",
                                           "image_comfy_workflow": "{not json"})
        assert "JSON معتبر نیست" in r.text
        client.post("/settings", data={"llm_provider": "fake", "image_provider": "off"})
