import base64
import json

import pytest

from app import v2ray
from app.config import settings


def test_parse_vless_reality():
    o = v2ray.parse("vless://11111111-2222-3333-4444-555555555555@srv.example.com:443?encryption=none"
                    "&flow=xtls-rprx-vision&security=reality&sni=www.speedtest.net&fp=chrome&pbk=PUBKEY&sid=ab12"
                    "&type=tcp#My%20Server")
    assert o["protocol"] == "vless" and o["_name"] == "My Server"
    user = o["settings"]["vnext"][0]["users"][0]
    assert user == {"id": "11111111-2222-3333-4444-555555555555", "encryption": "none", "flow": "xtls-rprx-vision"}
    s = o["streamSettings"]
    assert s["network"] == "tcp" and s["security"] == "reality"
    assert s["realitySettings"] == {"serverName": "www.speedtest.net", "fingerprint": "chrome", "publicKey": "PUBKEY",
                                    "shortId": "ab12", "spiderX": ""}


def test_parse_vless_ws_tls():
    s = v2ray.parse("vless://id@1.2.3.4:8443?security=tls&type=ws&host=cdn.example.com&path=%2Fws%3Fed%3D2048"
                    "&alpn=h2%2Chttp%2F1.1")["streamSettings"]
    assert s["wsSettings"] == {"path": "/ws?ed=2048", "host": "cdn.example.com"}
    assert s["tlsSettings"]["serverName"] == "cdn.example.com" and s["tlsSettings"]["alpn"] == ["h2", "http/1.1"]


def test_parse_vmess():
    j = {"v": "2", "ps": "vm", "add": "vm.example.com", "port": "443", "id": "uuid", "aid": "0", "net": "grpc",
         "path": "svc", "tls": "tls", "sni": "sni.example.com"}
    o = v2ray.parse("vmess://" + base64.b64encode(json.dumps(j).encode()).decode())
    assert o["settings"]["vnext"][0]["address"] == "vm.example.com" and o["settings"]["vnext"][0]["port"] == 443
    s = o["streamSettings"]
    assert s["grpcSettings"]["serviceName"] == "svc" and s["tlsSettings"]["serverName"] == "sni.example.com"


def test_parse_trojan_and_ss():
    t = v2ray.parse("trojan://pass@tr.example.com:443?sni=x.com#t")
    assert t["settings"]["servers"][0]["password"] == "pass" and t["streamSettings"]["security"] == "tls"
    ui = base64.urlsafe_b64encode(b"aes-256-gcm:secret").decode().rstrip("=")
    ss = v2ray.parse(f"ss://{ui}@ss.example.com:8388#s")["settings"]["servers"][0]
    assert (ss["method"], ss["password"], ss["port"]) == ("aes-256-gcm", "secret", 8388)
    legacy = v2ray.parse("ss://" + base64.b64encode(b"chacha20-ietf-poly1305:pw@9.9.9.9:1234").decode())
    assert legacy["settings"]["servers"][0]["address"] == "9.9.9.9"


def test_extract_links_and_errors():
    sub = base64.b64encode(b"vless://a@h:1#x\ntrojan://p@h:2\n").decode()
    assert v2ray.extract_links(sub) == ["vless://a@h:1#x", "trojan://p@h:2"]
    with pytest.raises(v2ray.V2rayError):
        v2ray.parse("http://example.com")
    with pytest.raises(v2ray.V2rayError):
        v2ray.parse("vless://id@h:443?security=reality")  # no public key


def test_config_and_proxy_selection(monkeypatch):
    cfg = v2ray.build_config(v2ray.parse("trojan://p@h.com:443"), 10809, 10808)
    assert cfg["inbounds"][0] == {"tag": "http", "listen": "127.0.0.1", "port": 10809, "protocol": "http"}
    assert "_name" not in cfg["outbounds"][0]
    monkeypatch.setattr(settings, "publish_proxy", "http://manual:3128")
    monkeypatch.setattr(settings, "v2ray_enabled", False)
    assert v2ray.publish_proxy() == "http://manual:3128"
    monkeypatch.setattr(settings, "v2ray_enabled", True)
    monkeypatch.setattr(settings, "v2ray_link", "trojan://p@h.com:443")
    monkeypatch.setattr(v2ray, "running", lambda: True)
    assert v2ray.publish_proxy() == "http://127.0.0.1:10809"
    assert v2ray.llm_proxy() is None


def test_settings_page_v2ray(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    started = []
    monkeypatch.setattr(v2ray, "start", lambda text: started.append(text))
    with TestClient(app) as client:
        r = client.post("/settings", data={"llm_provider": "fake", "v2ray_link": "not a link"})
        assert "نامعتبر" in r.text and not settings.v2ray_link
        r = client.post("/settings", data={"llm_provider": "fake", "v2ray_link": "trojan://p@h.com:443#srv"})
        assert "ذخیره شد" in r.text
        assert settings.v2ray_enabled and settings.v2ray_link == "trojan://p@h.com:443#srv"
        assert started == ["trojan://p@h.com:443#srv"]
        assert "srv" in client.get("/settings").text
        client.post("/settings", data={"llm_provider": "fake"})  # unchecked = off, link kept
        assert not settings.v2ray_enabled and settings.v2ray_link
        client.post("/settings", data={"llm_provider": "fake", "clear_v2ray_link": "1"})
        assert settings.v2ray_link == ""


def test_upload_xray(monkeypatch, tmp_path):
    import io
    import zipfile

    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(v2ray, "local_dir", lambda: str(tmp_path))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xray", b"\x7fELF-fake")
        z.writestr("geoip.dat", b"geo")
    with TestClient(app) as client:
        r = client.post("/v2ray-upload", files={"file": ("Xray-linux-64.zip", buf.getvalue())})
        assert "Xray نصب شد" in r.text
        assert (tmp_path / "xray").read_bytes() == b"\x7fELF-fake" and (tmp_path / "geoip.dat").exists()
        r = client.post("/v2ray-upload", files={"file": ("x.zip", b"junk")})
        assert "نامعتبر" in r.text
