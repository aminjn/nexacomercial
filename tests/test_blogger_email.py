import socket
import threading

import httpx
import pytest

from app import mailer
from app.config import settings
from app.content import Article
from app.publishers import make
from app.publishers.articles import BloggerEmail
from app.publishers.base import Publisher, PublishError
from tests.test_publishers import _mock

ART = Article(title="خرید آپارتمان در تهران", body_markdown="متن [ملکجت](https://melkjet.com)", excerpt="", tags=[],
              link_url="https://melkjet.com", anchor="ملکجت", site_id=1)


def test_blogger_by_email_publishes_and_finds_the_post(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, html: sent.append((to, subject, html)))

    def handler(req):
        assert req.url.path == "/feeds/posts/default"
        return httpx.Response(200, json={"feed": {"entry": [
            {"title": {"$t": "پست قدیمی"}, "id": {"$t": "tag:blogger.com,1999:blog-1.post-11"}, "link": []},
            {"title": {"$t": "خرید آپارتمان در تهران"}, "id": {"$t": "tag:blogger.com,1999:blog-1.post-22"},
             "link": [{"rel": "alternate", "href": "https://melkjet.blogspot.com/2026/10/post.html"}]}]}})
    monkeypatch.setattr(Publisher, "http", _mock(handler))
    monkeypatch.setattr("time.sleep", lambda s: None)
    pub = make("blogger_email", {"blog_email": "melkjet.abc@blogger.com", "blog_url": "melkjet.blogspot.com"})
    res = pub.publish_article(ART)
    assert sent[0][0] == "melkjet.abc@blogger.com" and sent[0][1] == "خرید آپارتمان در تهران"
    assert 'href="https://melkjet.com"' in sent[0][2]
    assert (res.url, res.external_id) == ("https://melkjet.blogspot.com/2026/10/post.html", "22")
    with pytest.raises(PublishError, match="@blogger.com"):
        make("blogger_email", {"blog_email": "x@gmail.com", "blog_url": "b.blogspot.com"}).publish_article(ART)


def test_mailer_builds_html_mail_and_reports_missing_settings(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    with pytest.raises(mailer.MailError, match="تنظیم نشده"):
        mailer.send("a@b.com", "s", "<p>x</p>")
    got = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            got["addr"] = (host, port)

        def starttls(self, context=None):
            got["tls"] = True

        def login(self, u, p):
            got["login"] = (u, p)

        def send_message(self, msg):
            got["msg"] = msg

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    for k, v in (("smtp_host", "smtp.gmail.com"), ("smtp_port", 587), ("smtp_user", "me@gmail.com"),
                 ("smtp_password", "app-pass"), ("smtp_via_v2ray", False)):
        monkeypatch.setattr(settings, k, v)
    mailer.send("melkjet.abc@blogger.com", "عنوان", "<h2>سلام</h2>")
    assert got["addr"] == ("smtp.gmail.com", 587) and got["tls"] and got["login"] == ("me@gmail.com", "app-pass")
    assert got["msg"]["To"] == "melkjet.abc@blogger.com" and got["msg"]["Subject"] == "عنوان"
    assert "<h2>سلام</h2>" in got["msg"].get_body(("html",)).get_content()


def test_socks5_handshake():
    """A tiny SOCKS5 server: checks the request and answers like Xray does."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen = {}

    def serve():
        c, _ = srv.accept()
        seen["greeting"] = c.recv(3)
        c.sendall(b"\x05\x00")
        seen["request"] = c.recv(64)
        c.sendall(b"\x05\x00\x00\x01" + bytes(4) + b"\x00\x00")
        c.sendall(b"220 smtp ready\r\n")
        c.close()
    threading.Thread(target=serve, daemon=True).start()
    s = mailer.socks5_connect("127.0.0.1", srv.getsockname()[1], "smtp.gmail.com", 587, 5)
    assert s.recv(64) == b"220 smtp ready\r\n"
    assert seen["greeting"] == b"\x05\x01\x00" and b"smtp.gmail.com" in seen["request"]
    assert seen["request"].endswith((587).to_bytes(2, "big"))
    s.close()
    srv.close()


def test_blogger_email_is_the_only_blogger_in_the_form():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        page = client.get("/accounts").text
        assert 'value="blogger_email"' in page and 'value="blogger"' not in page
        assert "ایمیل فرستنده" in client.get("/settings").text
    assert BloggerEmail.category == "article"
