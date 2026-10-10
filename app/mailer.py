"""Sending email (used for Blogger's "post by email"). One sender mailbox, set once on the settings page.
Goes through v2ray's SOCKS port when it is on (Gmail and most foreign mail servers are blocked from Iran)."""
from __future__ import annotations

import smtplib
import socket
import ssl
import struct
from email.message import EmailMessage
from email.utils import make_msgid

from . import v2ray
from .config import settings


class MailError(RuntimeError):
    pass


def socks5_connect(proxy_host: str, proxy_port: int, host: str, port: int, timeout: float) -> socket.socket:
    """Open a TCP connection to host:port through a SOCKS5 proxy (no auth) — enough for the local Xray."""
    s = socket.create_connection((proxy_host, proxy_port), timeout)
    s.sendall(b"\x05\x01\x00")
    if s.recv(2) != b"\x05\x00":
        s.close()
        raise MailError("پروکسی SOCKS اتصال را قبول نکرد")
    name = host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port))
    head = s.recv(4)
    if len(head) < 4 or head[1] != 0:
        s.close()
        raise MailError(f"پروکسی به {host}:{port} وصل نشد")
    skip = {1: 4, 3: None, 4: 16}.get(head[3], 4)
    if skip is None:
        skip = s.recv(1)[0]
    s.recv(skip + 2)  # bound address + port
    return s


def _via_proxy() -> bool:
    return bool(settings.smtp_via_v2ray and v2ray.proxy_url() and v2ray.running())


class _Proxied:
    """Mixin: smtplib asks _get_socket for its TCP connection; hand it one through v2ray."""

    def _get_socket(self, host, port, timeout):  # type: ignore[no-untyped-def]
        sock = socks5_connect("127.0.0.1", settings.v2ray_socks_port, host, port, timeout)
        if isinstance(self, smtplib.SMTP_SSL):
            return self.context.wrap_socket(sock, server_hostname=host)
        return sock


class _SMTP(_Proxied, smtplib.SMTP):
    pass


class _SMTP_SSL(_Proxied, smtplib.SMTP_SSL):
    pass


def configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def send(to: str, subject: str, html: str, timeout: float = 60) -> None:
    if not configured():
        raise MailError("ایمیل فرستنده تنظیم نشده: در صفحه‌ی تنظیمات ← «ایمیل فرستنده» را پر کن")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content("This post needs an HTML capable reader.")
    msg.add_alternative(html, subtype="html")
    port = int(settings.smtp_port or 587)
    ssl_ctx = ssl.create_default_context()
    proxied = _via_proxy()
    try:
        if port == 465:
            cls = _SMTP_SSL if proxied else smtplib.SMTP_SSL
            server = cls(settings.smtp_host, port, timeout=timeout, context=ssl_ctx)
        else:
            server = (_SMTP if proxied else smtplib.SMTP)(settings.smtp_host, port, timeout=timeout)
            server.starttls(context=ssl_ctx)
        with server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("نام کاربری یا رمز ایمیل فرستنده درست نیست (برای Gmail باید «App password» بسازی)") from e
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(f"ایمیل فرستاده نشد: {type(e).__name__}: {e}") from e
