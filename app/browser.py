"""Headless Chromium for "log in as a user" accounts.

You log in once yourself through a live view of the browser in the dashboard (so 2FA codes and
captchas are handled by you); the cookies are saved encrypted per account and every later post
reuses them. All traffic goes through v2ray / the publish proxy.

Playwright objects belong to the thread that created them, so one worker thread owns the browser and
everything else hands it jobs through `call()`.
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
import os
import queue
import shutil
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from . import crypto, v2ray
from .config import settings

log = logging.getLogger(__name__)

VIEWPORT = {"width": 1100, "height": 820}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
LOGIN_IDLE_SEC = 15 * 60


class BrowserError(RuntimeError):
    pass


# ---------------------------------------------------------------- saved sessions


def _session_file(account_id: int):
    d = settings.data_path / "sessions"
    d.mkdir(exist_ok=True)
    return d / f"{int(account_id)}.bin"


def load_state(account_id: int) -> dict[str, Any] | None:
    f = _session_file(account_id)
    if not f.exists():
        return None
    try:
        return crypto.decrypt(f.read_text())
    except Exception:  # noqa: BLE001 — unreadable (secret key changed): treat as logged out
        return None


def save_state(account_id: int, state: dict[str, Any]) -> None:
    f = _session_file(account_id)
    f.write_text(crypto.encrypt(state))
    os.chmod(f, 0o600)


def forget(account_id: int) -> None:
    _session_file(account_id).unlink(missing_ok=True)


def error_shot(account_id: int):
    """Screenshot of the page where the last browser publish failed."""
    return settings.data_path / "sessions" / f"{int(account_id)}-error.jpg"


def last_shot(account_id: int):
    """Screenshot taken right after the last successful browser publish."""
    return settings.data_path / "sessions" / f"{int(account_id)}-last.jpg"


_SAMESITE = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}


def parse_cookies(text: str) -> list[dict[str, Any]]:
    """Cookies exported from your own browser: Cookie-Editor / EditThisCookie JSON, or a Netscape cookies.txt."""
    import json
    text = (text or "").strip()
    out: list[dict[str, Any]] = []
    if text.startswith(("[", "{")):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise BrowserError("متن کوکی JSON درستی نیست؛ دوباره از افزونه Export → JSON بگیر") from e
        if isinstance(data, dict):
            data = data.get("cookies", [])
        for c in data:
            if not isinstance(c, dict) or not c.get("name") or not c.get("domain"):
                continue
            exp = c.get("expirationDate", c.get("expires", -1))
            ck = {"name": str(c["name"]), "value": str(c.get("value", "")), "domain": str(c["domain"]),
                  "path": str(c.get("path") or "/"), "expires": float(exp) if exp not in (None, "") else -1,
                  "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure"))}
            ss = _SAMESITE.get(str(c.get("sameSite") or "").lower())
            if ss:
                ck["sameSite"] = ss
                if ss == "None":
                    ck["secure"] = True
            out.append(ck)
    else:
        for line in text.splitlines():
            httponly = line.startswith("#HttpOnly_")
            if httponly:
                line = line[len("#HttpOnly_"):]
            parts = line.split("\t")
            if line.startswith("#") or len(parts) < 7:
                continue
            domain, _, path, secure, exp, name, value = parts[:7]
            out.append({"name": name, "value": value.strip(), "domain": domain, "path": path or "/",
                        "expires": float(exp) if exp.strip() not in ("", "0") else -1,
                        "httpOnly": httponly, "secure": secure.upper() == "TRUE"})
    if not out:
        raise BrowserError("هیچ کوکی‌ای در این متن پیدا نشد")
    return out


def import_cookies(account_id: int, text: str) -> int:
    """Log in with cookies from your own browser (for sites like Google that refuse to log in on the server)."""
    new = parse_cookies(text)
    state = load_state(account_id) or {"cookies": [], "origins": []}
    key = lambda c: (c["name"], c["domain"].lstrip("."), c.get("path", "/"))  # noqa: E731
    fresh = {key(c) for c in new}
    state["cookies"] = [c for c in state.get("cookies", []) if key(c) not in fresh] + new
    state.setdefault("origins", [])
    save_state(account_id, state)
    return len(new)


def has_session(account_id: int) -> bool:
    return _session_file(account_id).exists()


# ---------------------------------------------------------------- worker thread


_jobs: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_pw: Any = None
_browsers: dict[bool, tuple[Any, str]] = {}  # direct? → (browser, proxy it was launched with)


def _loop() -> None:
    while True:
        fn, args, fut = _jobs.get()
        if fut.set_running_or_notify_cancel():
            try:
                fut.set_result(fn(*args))
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)
        _reap_idle_logins()


def call(fn: Callable[..., Any], *args: Any, timeout: float = 600) -> Any:
    """Run `fn(*args)` on the browser thread and return its result."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="browser", daemon=True)
            _worker.start()
    fut: cf.Future = cf.Future()
    _jobs.put((fn, args, fut))
    return fut.result(timeout)


def chromium_path() -> str | None:
    return settings.chromium_path or shutil.which("chromium") or shutil.which("chromium-browser") or None


def _proxy() -> dict[str, str] | None:
    url = v2ray.publish_proxy()
    if not url:
        return None
    u = urlsplit(url)
    p = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        p.update(username=u.username, password=u.password or "")
    return p


def _get_browser(direct: bool = False) -> Any:
    """Launch (or relaunch after a proxy change / crash) the shared browser. Browser thread only.
    direct=True: a second browser without v2ray, for Iranian sites that refuse foreign addresses."""
    global _pw
    proxy = None if direct else _proxy()
    key = repr(proxy)
    current, current_key = _browsers.get(direct, (None, ""))
    if current is not None and current.is_connected() and current_key == key:
        return current
    if current is not None:
        try:
            current.close()
        except Exception:  # noqa: BLE001
            pass
        for aid in [a for a, s in _logins.items() if s.get("direct") == direct]:
            _logins.pop(aid, None)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserError("مرورگر روی سرور نصب نیست. روی سرور بزن: "
                           "INSTALL_BROWSER=1 APT_MIRROR=https://mirror.arvancloud.ir docker compose up -d --build") from e
    if _pw is None:
        _pw = sync_playwright().start()
    try:
        b = _pw.chromium.launch(
            executable_path=chromium_path(), headless=True, proxy=proxy,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=en-US"])
    except Exception as e:  # noqa: BLE001
        if chromium_path() is None and "Executable doesn't exist" in str(e):
            raise BrowserError("مرورگر Chromium روی سرور نصب نیست. روی سرور بزن: "
                               "INSTALL_BROWSER=1 APT_MIRROR=https://mirror.arvancloud.ir docker compose up -d --build") from e
        raise BrowserError(f"مرورگر Chromium اجرا نشد: {str(e).splitlines()[0][:300]}") from e
    _browsers[direct] = (b, key)
    return b


def new_context(state: dict[str, Any] | None = None, direct: bool = False) -> Any:
    ctx = _get_browser(direct).new_context(storage_state=state, viewport=VIEWPORT, user_agent=UA, locale="en-US")
    ctx.set_default_timeout(30_000)
    return ctx


def available() -> tuple[bool, str]:
    try:
        call(_get_browser, timeout=120)
        return True, "مرورگر آماده است"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# ---------------------------------------------------------------- live login from the dashboard


_logins: dict[int, dict[str, Any]] = {}


def _reap_idle_logins() -> None:
    for aid, s in list(_logins.items()):
        if time.time() - s["last"] > LOGIN_IDLE_SEC:
            _close_login(aid)


def _close_login(account_id: int) -> None:
    s = _logins.pop(account_id, None)
    if s:
        try:
            s["ctx"].close()
        except Exception:  # noqa: BLE001
            pass


def _login(account_id: int) -> dict[str, Any]:
    s = _logins.get(account_id)
    if s is None:
        raise BrowserError("پنجره‌ی ورود بسته شده؛ دوباره «ورود با مرورگر» را بزن")
    s["last"] = time.time()
    return s


def _start(account_id: int, url: str, direct: bool = False) -> None:
    _close_login(account_id)
    ctx = new_context(load_state(account_id), direct=direct)
    page = ctx.new_page()
    _logins[account_id] = {"ctx": ctx, "page": page, "last": time.time(), "direct": direct}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:  # noqa: BLE001 — keep the window open; the user can retry from the address bar
        log.warning("login %s: %s", account_id, e)


def _shot(account_id: int) -> bytes:
    page = _login(account_id)["page"]
    return page.screenshot(type="jpeg", quality=60, timeout=15_000)


def _act(account_id: int, a: dict[str, Any]) -> str:
    page = _login(account_id)["page"]
    kind = a.get("action")
    if kind == "click":
        page.mouse.click(float(a["x"]), float(a["y"]))
    elif kind == "type":
        page.keyboard.insert_text(str(a.get("text", "")))
    elif kind == "key":
        key = str(a.get("key", ""))
        if key not in ("Enter", "Backspace", "Tab", "Escape", "ArrowDown", "ArrowUp", "Delete"):
            raise BrowserError("کلید نامعتبر")
        page.keyboard.press(key)
    elif kind == "scroll":
        page.mouse.wheel(0, float(a.get("dy", 400)))
    elif kind == "goto":
        url = str(a.get("url", "")).strip()
        page.goto(url if "://" in url else "https://" + url, wait_until="domcontentloaded", timeout=60_000)
    elif kind == "back":
        page.go_back(wait_until="domcontentloaded")
    elif kind == "reload":
        page.reload(wait_until="domcontentloaded")
    else:
        raise BrowserError("دستور نامعتبر")
    page.wait_for_timeout(400)
    return page.url


def _finish(account_id: int) -> None:
    s = _login(account_id)
    save_state(account_id, s["ctx"].storage_state())
    _close_login(account_id)


def login_start(account_id: int, url: str, direct: bool = False) -> None:
    call(_start, account_id, url, direct, timeout=120)


def login_shot(account_id: int) -> bytes:
    return call(_shot, account_id, timeout=30)


def login_act(account_id: int, action: dict[str, Any]) -> str:
    return call(_act, account_id, action, timeout=90)


def login_finish(account_id: int) -> None:
    call(_finish, account_id, timeout=30)


def login_cancel(account_id: int) -> None:
    call(_close_login, account_id, timeout=30)


def login_active(account_id: int) -> bool:
    return account_id in _logins


# ---------------------------------------------------------------- running a publishing recipe


def run_with_session(account_id: int, recipe: Callable[[Any], Any], timeout: float = 300,
                     keep_shot: bool = True, direct: bool = False) -> Any:
    """Open a page with the account's saved cookies, run `recipe(page)`, save refreshed cookies."""
    state = load_state(account_id)
    if state is None:
        raise BrowserError("هنوز وارد این اکانت نشده‌ای: در صفحه‌ی اکانت‌ها «ورود با مرورگر» را بزن")

    def job() -> Any:
        ctx = new_context(state, direct=direct)
        try:
            page = ctx.new_page()
            result = recipe(page)
            save_state(account_id, ctx.storage_state())
            try:  # what the page looked like right after publishing, to check it from the dashboard
                if keep_shot:
                    ctx.pages[-1].screenshot(path=str(last_shot(account_id)), type="jpeg", quality=60)
            except Exception:  # noqa: BLE001
                pass
            return result
        except Exception:
            try:
                ctx.pages[-1].screenshot(path=str(error_shot(account_id)), type="jpeg", quality=60)
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            ctx.close()

    return call(job, timeout=timeout)
