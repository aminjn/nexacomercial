"""Built-in v2ray client: turns a share link (vless / vmess / trojan / ss) into an Xray config, runs Xray
in the background and exposes a local HTTP proxy that publishing (and optionally AI) requests go through."""
from __future__ import annotations

import base64
import collections
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from . import xray_get
from .config import settings

log = logging.getLogger(__name__)

PROTOCOLS = ("vless://", "vmess://", "trojan://", "ss://")


class V2rayError(ValueError):
    pass


# ---------------------------------------------------------------- parsing share links


def _b64(s: str) -> str:
    s = s.strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")


def extract_links(text: str) -> list[str]:
    """Share links in pasted text: one per line, or a base64 subscription body."""
    text = text.strip()
    if text and "://" not in text:
        try:
            text = _b64(text)
        except (ValueError, UnicodeDecodeError):
            pass
    return [ln.strip() for ln in text.splitlines() if ln.strip().lower().startswith(PROTOCOLS)]


def _stream(p: dict[str, str], host: str) -> dict[str, Any]:
    """streamSettings from the common share-link query parameters."""
    net = (p.get("type") or p.get("net") or "tcp").lower()
    net = {"splithttp": "xhttp", "h2": "http"}.get(net, net)
    sec = (p.get("security") or p.get("tls") or "none").lower()
    s: dict[str, Any] = {"network": net, "security": sec if sec in ("tls", "reality") else "none"}
    hhost, path = p.get("host", ""), unquote(p.get("path", "")) or "/"
    if net == "ws":
        s["wsSettings"] = {"path": path, **({"host": hhost} if hhost else {})}
    elif net == "grpc":
        s["grpcSettings"] = {"serviceName": p.get("serviceName") or p.get("path", ""),
                             "multiMode": p.get("mode") == "multi"}
    elif net == "httpupgrade":
        s["httpupgradeSettings"] = {"path": path, **({"host": hhost} if hhost else {})}
    elif net == "xhttp":
        s["xhttpSettings"] = {"path": path, "mode": p.get("mode") or "auto", **({"host": hhost} if hhost else {})}
    elif net == "http":
        s["httpSettings"] = {"path": path, **({"host": hhost.split(",")} if hhost else {})}
    elif net == "kcp":
        s["kcpSettings"] = {"header": {"type": p.get("headerType") or "none"},
                            **({"seed": p["seed"]} if p.get("seed") else {})}
    elif net == "tcp" and p.get("headerType") == "http":
        s["tcpSettings"] = {"header": {"type": "http", "request": {
            "path": [path], "headers": {"Host": hhost.split(",") if hhost else [host]}}}}
    sni = p.get("sni") or p.get("peer") or hhost.split(",")[0] or host
    fp = p.get("fp", "")
    if s["security"] == "tls":
        tls: dict[str, Any] = {"serverName": sni}  # allowInsecure: removed in current Xray, ignored
        if fp:
            tls["fingerprint"] = fp
        if p.get("alpn"):
            tls["alpn"] = unquote(p["alpn"]).split(",")
        s["tlsSettings"] = tls
    elif s["security"] == "reality":
        if not p.get("pbk"):
            raise V2rayError("لینک reality کلید عمومی (pbk) ندارد")
        s["realitySettings"] = {"serverName": sni, "fingerprint": fp or "chrome", "publicKey": p["pbk"],
                                "shortId": p.get("sid", ""), "spiderX": unquote(p.get("spx", ""))}
    return s


def parse(link: str) -> dict[str, Any]:
    """Share link -> Xray outbound (with "_name" for display)."""
    link = link.strip()
    scheme = link.split("://", 1)[0].lower()
    if scheme == "vmess":
        try:
            j = json.loads(_b64(link[8:].split("#")[0]))
        except (ValueError, UnicodeDecodeError) as e:
            raise V2rayError("لینک vmess خراب است") from e
        p = {k: str(v) for k, v in j.items() if v not in (None, "")}
        host, port = p.get("add", ""), int(p.get("port", 0) or 0)
        if p.get("net") in ("tcp", None) and p.get("type") == "http":
            p["headerType"] = "http"
        p.pop("type", None)
        out = {"protocol": "vmess", "settings": {"vnext": [{"address": host, "port": port, "users": [
            {"id": p.get("id", ""), "alterId": int(p.get("aid", 0) or 0), "security": p.get("scy") or "auto"}]}]},
            "streamSettings": _stream(p, host), "_name": p.get("ps", "")}
    elif scheme in ("vless", "trojan", "ss"):
        u = urlsplit(link)
        name = unquote(u.fragment)
        p = {k: v[0] for k, v in parse_qs(u.query).items()}
        userinfo, host, port = unquote(u.username or ""), u.hostname or "", u.port or 0
        if scheme == "ss":
            if "@" not in link[5:].split("#")[0]:  # legacy: ss://base64(method:pass@host:port)
                try:
                    u = urlsplit("ss://" + _b64(link[5:].split("#")[0]))
                except (ValueError, UnicodeDecodeError) as e:
                    raise V2rayError("لینک shadowsocks خراب است") from e
                host, port = u.hostname or "", u.port or 0
                userinfo = unquote(u.username or "") + ":" + unquote(u.password or "")
            elif ":" not in userinfo and not u.password:
                try:
                    userinfo = _b64(userinfo)
                except (ValueError, UnicodeDecodeError) as e:
                    raise V2rayError("لینک shadowsocks خراب است") from e
            elif u.password:
                userinfo = f"{userinfo}:{unquote(u.password)}"
            method, _, password = userinfo.partition(":")
            out = {"protocol": "shadowsocks", "settings": {"servers": [
                {"address": host, "port": port, "method": method, "password": password}]}}
            if p.get("plugin"):
                raise V2rayError("لینک shadowsocks با plugin پشتیبانی نمی‌شود")
        elif scheme == "vless":
            user: dict[str, Any] = {"id": userinfo, "encryption": p.get("encryption") or "none"}
            if p.get("flow"):
                user["flow"] = p["flow"]
            out = {"protocol": "vless", "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
                   "streamSettings": _stream(p, host)}
        else:
            p.setdefault("security", "tls")
            out = {"protocol": "trojan", "settings": {"servers": [{"address": host, "port": port, "password": userinfo}]},
                   "streamSettings": _stream(p, host)}
        out["_name"] = name
    else:
        raise V2rayError("لینک باید با vless:// یا vmess:// یا trojan:// یا ss:// شروع شود")
    if not host or not port:
        raise V2rayError("آدرس یا پورت سرور در لینک نیست")
    out["_server"] = f"{scheme} — {host}:{port}"
    return out


def build_config(outbound: dict[str, Any], http_port: int, socks_port: int) -> dict[str, Any]:
    ob = {k: v for k, v in outbound.items() if not k.startswith("_")}
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "http", "listen": "127.0.0.1", "port": http_port, "protocol": "http"},
            {"tag": "socks", "listen": "127.0.0.1", "port": socks_port, "protocol": "socks",
             "settings": {"udp": True}},
        ],
        "outbounds": [{**ob, "tag": "proxy"}, {"protocol": "freedom", "tag": "direct"}],
    }


def describe(text: str) -> str:
    """Short label of the configured server for the settings page."""
    try:
        links = extract_links(text)
        o = parse(links[0])
        return " ".join(x for x in (o["_name"], f"({o['_server']})") if x)
    except (V2rayError, IndexError):
        return "لینک نامعتبر"


# ---------------------------------------------------------------- running Xray


_lock = threading.RLock()
_proc: subprocess.Popen | None = None
_logs: collections.deque[str] = collections.deque(maxlen=60)
_state: dict[str, Any] = {"link": "", "error": "", "last_start": 0.0}


def local_dir() -> str:
    return str(settings.data_path / "xray")


def binary() -> str | None:
    for cand in (settings.xray_bin, os.path.join(local_dir(), "xray"), shutil.which("xray") or ""):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def install_binary(url: str = "") -> str:
    return xray_get.install(local_dir(), url or settings.xray_download_url, proxy=settings.publish_proxy)


def install_upload(data: bytes) -> str:
    """Xray release zip (or the bare binary) uploaded from the dashboard."""
    return xray_get.extract(data, local_dir())


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def _reader(proc: subprocess.Popen) -> None:
    for line in proc.stdout or []:
        _logs.append(line.rstrip())


def stop() -> None:
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(5)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None


def start(text: str) -> None:
    """(Re)start Xray with the first link in `text`. Raises V2rayError with a Persian message on failure."""
    global _proc
    with _lock:
        stop()
        _state.update(link=text, error="", last_start=time.time())
        links = extract_links(text)
        if not links:
            raise V2rayError("هیچ لینک vless/vmess/trojan/ss پیدا نشد")
        cfg = build_config(parse(links[0]), settings.v2ray_http_port, settings.v2ray_socks_port)
        exe = binary()
        if not exe:
            raise V2rayError("برنامه‌ی Xray نصب نیست — دکمه‌ی «نصب Xray» را بزن")
        path = settings.data_path / "v2ray.json"
        path.write_text(json.dumps(cfg, indent=2))
        os.chmod(path, 0o600)
        env = {**os.environ, "XRAY_LOCATION_ASSET": os.path.dirname(exe)}
        _logs.clear()
        _proc = subprocess.Popen([exe, "run", "-c", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, env=env)
        threading.Thread(target=_reader, args=(_proc,), daemon=True).start()
        time.sleep(1.0)
        if _proc.poll() is not None:
            time.sleep(0.2)
            raise V2rayError("Xray اجرا نشد: " + " | ".join(list(_logs)[-5:]))
        log.info("v2ray started: %s", describe(text))


def sync() -> str:
    """Bring the running Xray in line with the saved settings. Returns an error message ("" = fine)."""
    with _lock:
        if not (settings.v2ray_enabled and settings.v2ray_link):
            stop()
            _state.update(link="", error="")
            return ""
        if running() and _state["link"] == settings.v2ray_link:
            return ""
        try:
            start(settings.v2ray_link)
        except (V2rayError, OSError) as e:
            stop()
            _state["error"] = str(e)
            log.warning("v2ray: %s", e)
        return _state["error"]


def proxy_url() -> str:
    """Local HTTP proxy of the running Xray ("" when v2ray is off). Restarts Xray if it has died."""
    if not (settings.v2ray_enabled and settings.v2ray_link):
        return ""
    if not running() and time.time() - _state["last_start"] > 30:
        sync()
    return f"http://127.0.0.1:{settings.v2ray_http_port}"


def publish_proxy() -> str:
    """Proxy for publishing / link checking: v2ray when on, else the manual proxy from settings."""
    return proxy_url() or settings.publish_proxy


def llm_proxy() -> str | None:
    return (publish_proxy() or None) if settings.v2ray_for_llm else None


def status() -> dict[str, Any]:
    return {"enabled": settings.v2ray_enabled and bool(settings.v2ray_link), "running": running(),
            "error": _state["error"], "server": describe(settings.v2ray_link) if settings.v2ray_link else "",
            "binary": binary(), "logs": list(_logs)[-15:]}


def test(timeout: float = 20) -> tuple[bool, str]:
    """Fetch a page through the tunnel and report the exit IP / country."""
    proxy = proxy_url()
    if not proxy:
        return False, "v2ray روشن نیست"
    if not running():
        return False, _state["error"] or "Xray اجرا نیست"
    t = time.time()
    try:
        r = httpx.get("https://cloudflare.com/cdn-cgi/trace", proxy=proxy, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as e:
        tail = " | ".join(list(_logs)[-3:])
        return False, f"از طریق v2ray به اینترنت وصل نشد: {type(e).__name__}: {e}" + (f" — لاگ Xray: {tail}" if tail else "")
    info = dict(ln.split("=", 1) for ln in r.text.splitlines() if "=" in ln)
    return True, f"وصل است ✓ آی‌پی خروجی {info.get('ip', '?')} ({info.get('loc', '?')})، {int((time.time() - t) * 1000)} میلی‌ثانیه"
