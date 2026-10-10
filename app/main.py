"""FastAPI app: dashboard to manage sites, accounts and campaigns + JSON API + background scheduler."""
from __future__ import annotations

import datetime as dt
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from . import browser, engine, importer, runtime, scheduler, stats, v2ray
from . import models as m
from .config import settings
from .publishers import REGISTRY, make
from .publishers.fields import KIND_FIELDS, KIND_GUIDES, field_names, is_secret, masked, missing_fields

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic(auto_error=False)


def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not settings.admin_password:
        return
    ok = creds is not None and secrets.compare_digest(creds.username, settings.admin_user) \
        and secrets.compare_digest(creds.password, settings.admin_password)
    if not ok:
        raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    m.engine()
    runtime.apply()
    v2ray.sync()
    if settings.scheduler_enabled:
        scheduler.start()
    yield
    scheduler.stop()
    v2ray.stop()


app = FastAPI(title="Nexa Backlink", lifespan=lifespan)
# Uploaded images are public on purpose: Instagram / Pinterest servers download them by URL.
app.mount("/media", StaticFiles(directory=str(settings.uploads_path)), name="media")
protected = [Depends(auth)]


def local(t: dt.datetime | None) -> str:
    if not t:
        return "—"
    return t.replace(tzinfo=dt.timezone.utc).astimezone(engine.tz()).strftime("%Y-%m-%d %H:%M")


templates.env.filters["local"] = local
templates.env.globals["checkable"] = engine.checkable
templates.env.globals["is_post_url"] = stats.is_post_url


def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("msg", request.query_params.get("msg", ""))
    return templates.TemplateResponse(request, name, {"settings": settings, **ctx})


def back(url: str, msg: str = "") -> RedirectResponse:
    from urllib.parse import quote
    path, _, frag = url.partition("#")
    if msg:
        path += ("&" if "?" in path else "?") + f"msg={quote(msg)}"
    return RedirectResponse(path + (f"#{frag}" if frag else ""), status_code=303)


def _list(v: str) -> list[str]:
    return importer._list(v)


def _get(model, obj_id: int):
    with m.session() as s:
        obj = s.get(model, obj_id)
    if obj is None:
        raise HTTPException(404)
    return obj


@app.get("/health")
def health() -> dict:
    return {"ok": True, "dry_run": settings.dry_run, "llm": settings.llm_provider}


# ---------------------------------------------------------------- dashboard


@app.get("/", response_class=HTMLResponse, dependencies=protected)
def dashboard(request: Request):
    zone = engine.tz()
    today = m.day_start_utc(zone)
    with m.session() as s:
        sites = {x.id: x for x in s.exec(select(m.Site)).all()}
        campaigns = s.exec(select(m.Campaign)).all()
        rows = [{"c": c, "site": sites.get(c.site_id), "today": m.count_since(s, today, campaign_id=c.id),
                 "accounts": len(engine.campaign_accounts(s, c)), "in_window": engine.in_window(c)}
                for c in campaigns]
        accounts = s.exec(select(m.Account)).all()
        pubs = s.exec(select(m.Publication).order_by(m.Publication.created_at.desc()).limit(50)).all()
        logs = s.exec(select(m.RunLog).order_by(m.RunLog.created_at.desc()).limit(30)).all()
        stats = {
            "sites": len(sites), "accounts": len(accounts),
            "accounts_ok": sum(a.usable for a in accounts),
            "accounts_paused": sum(a.status == "paused" for a in accounts),
            "published": m.count_since(s, dt.datetime.min, status="ok"),
            "today": m.count_since(s, today, status="ok"),
            "live": len(s.exec(select(m.Publication).where(m.Publication.link_found == True)).all()),  # noqa: E712
        }
    return render(request, "dashboard.html", rows=rows, pubs=pubs, logs=logs, stats=stats, sites=sites)


@app.post("/tick", dependencies=protected)
def tick_now(dry: bool = False):
    recs = engine.tick(dry_run=dry or None)
    return back("/", f"{len(recs)} مورد اجرا شد")


@app.post("/llm-test", dependencies=protected)
def llm_test(next: str = "/"):
    from .llm import llm_check
    ok, msg = llm_check()
    return back(next if next.startswith("/") else "/", ("هوش مصنوعی: " if ok else "هوش مصنوعی وصل نیست: ") + msg)


# ---------------------------------------------------------------- settings (AI, proxy, ...)


@app.get("/settings", response_class=HTMLResponse, dependencies=protected)
def settings_page(request: Request):
    return render(request, "settings.html", has_llm_key=bool(settings.llm_api_key),
                  has_anthropic_key=bool(settings.anthropic_api_key), has_proxy=bool(settings.publish_proxy),
                  v2=v2ray.status())


V2RAY_PAGE = "/settings?at=v2ray#v2ray"
# replaced by their *_web version (no developer app / token needed). The Telegram bot stays: it is easy and reliable.
HIDDEN_KINDS = {k for k in REGISTRY if f"{k}_web" in REGISTRY and k != "telegram"}


@app.post("/settings", dependencies=protected)
async def settings_save(request: Request):
    f = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    provider = f.get("llm_provider", "ollama")
    if provider not in ("ollama", "openai", "anthropic", "fake"):
        return back("/settings", "نوع هوش مصنوعی نامعتبر است")
    values: dict[str, Any] = {
        "llm_provider": provider,
        "llm_base_url": f.get("llm_base_url", "").strip(),
        "llm_model": f.get("llm_model", "").strip(),
        "anthropic_model": f.get("anthropic_model", "").strip() or settings.anthropic_model,
        "llm_timeout_sec": float(f.get("llm_timeout_sec") or 600),
        "public_url": f.get("public_url", "").strip().rstrip("/"),
        "dry_run": bool(f.get("dry_run")),
        "v2ray_enabled": bool(f.get("v2ray_enabled")),
        "v2ray_for_llm": bool(f.get("v2ray_for_llm")),
    }
    # secrets: empty field = keep; "clear" checkbox = remove
    for key in ("llm_api_key", "anthropic_api_key", "publish_proxy", "v2ray_link"):
        v = f.get(key, "").strip()
        if v or f.get(f"clear_{key}"):
            values[key] = v
    if values.get("v2ray_link"):
        try:
            v2ray.parse_all(v2ray.resolve(values["v2ray_link"]))
        except v2ray.V2rayError as e:
            return back(V2RAY_PAGE, f"کانفیگ v2ray ذخیره نشد: {e}")
        values["v2ray_enabled"] = True  # a freshly pasted profile is meant to be used
    runtime.save(values)
    err = v2ray.sync()
    if err or "v2ray_link" in values:
        return back(V2RAY_PAGE, "تنظیمات ذخیره شد" + (f" — ولی v2ray اجرا نشد: {err}" if err else
                                                     f" — v2ray روشن شد: {v2ray.status()['server']}"))
    return back("/settings", "تنظیمات ذخیره شد")


@app.post("/v2ray-test", dependencies=protected)
def v2ray_test():
    v2ray.sync()
    ok, msg = v2ray.test()
    return back(V2RAY_PAGE, "v2ray: " + msg)


@app.post("/v2ray-install", dependencies=protected)
def v2ray_install():
    try:
        path = v2ray.install_binary()
    except Exception as e:  # noqa: BLE001
        return back(V2RAY_PAGE, f"دانلود Xray نشد: {type(e).__name__}: {e} — اگر گیت‌هاب از سرور باز نمی‌شود، "
                                 f"فایل xray را دستی در {v2ray.local_dir()} بگذار")
    err = v2ray.sync()
    return back(V2RAY_PAGE, f"Xray نصب شد ({path})" + (f" — ولی اجرا نشد: {err}" if err else ""))


@app.post("/v2ray-upload", dependencies=protected)
async def v2ray_upload(request: Request):
    upload = (await request.form()).get("file")
    data = await upload.read() if upload is not None and not isinstance(upload, str) else b""
    if not data:
        return back(V2RAY_PAGE, "فایلی انتخاب نشد")
    try:
        path = v2ray.install_upload(data)
    except (ValueError, OSError) as e:
        return back(V2RAY_PAGE, f"فایل Xray نامعتبر است: {e}")
    err = v2ray.sync()
    return back(V2RAY_PAGE, f"Xray نصب شد ({path})" + (f" — ولی اجرا نشد: {err}" if err else ""))


@app.get("/settings/models", dependencies=protected)
def settings_models(provider: str, base_url: str, api_key: str = ""):
    from .llm import list_models
    try:
        return {"models": list_models(provider, base_url, api_key or settings.llm_api_key)}
    except Exception as e:  # noqa: BLE001
        return {"models": [], "error": engine.explain_error("ai", "", e)}


@app.post("/accounts/resume-all", dependencies=protected)
def accounts_resume_all():
    with m.session() as s:
        for a in s.exec(select(m.Account).where(m.Account.status == "paused")).all():
            a.status, a.fail_count, a.last_error = "ok", 0, ""
            s.add(a)
        s.commit()
    return back("/accounts", "همه‌ی اکانت‌های متوقف دوباره فعال شدند")


@app.post("/verify", dependencies=protected)
def verify():
    return back("/", f"{engine.verify_links(max_age_hours=0)} لینک بررسی شد")


# ---------------------------------------------------------------- sites


@app.get("/sites", response_class=HTMLResponse, dependencies=protected)
def sites_page(request: Request, edit: int = 0):
    with m.session() as s:
        sites = s.exec(select(m.Site)).all()
        counts = {x.id: m.count_since(s, dt.datetime.min, site_id=x.id, status="ok") for x in sites}
    current = next((x for x in sites if x.id == edit), None)
    return render(request, "sites.html", sites=sites, counts=counts, current=current)


def _site_from_form(f: dict[str, str], site: m.Site | None = None) -> m.Site:
    pages = [{"url": u} for u in f.get("pages", "").split()]
    data = dict(name=f["name"].strip(), url=f["url"].strip(), language=f.get("language", "fa").strip() or "fa",
                niche=f.get("niche", ""), description=f.get("description", ""), keywords=_list(f.get("keywords", "")),
                anchors=_list(f.get("anchors", "")), pages=pages, style=f.get("style", ""), enabled=bool(f.get("enabled")))
    if site is None:
        return m.Site(**data)
    for k, v in data.items():
        setattr(site, k, v)
    return site


IMAGE_TYPES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"GIF8": ".gif", b"RIFF": ".webp"}


def public_base(request: Request) -> str:
    if settings.public_url:
        return settings.public_url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


async def save_image(upload: Any, request: Request) -> str:
    """Store an uploaded image under data/uploads and return its public URL."""
    data = await upload.read()
    if len(data) > settings.upload_max_mb * 1024 * 1024:
        raise ValueError(f"حجم تصویر بیشتر از {settings.upload_max_mb} مگابایت است")
    ext = next((e for sig, e in IMAGE_TYPES.items() if data.startswith(sig)), None)
    if ext is None or (ext == ".webp" and data[8:12] != b"WEBP"):
        raise ValueError("فقط تصویر JPG، PNG، WEBP یا GIF")
    name = secrets.token_hex(12) + ext
    (settings.uploads_path / name).write_bytes(data)
    return f"{public_base(request)}/media/{name}"


def delete_image(url: str) -> None:
    if "/media/" in url:
        f = settings.uploads_path / Path(url.rsplit("/media/", 1)[1]).name
        f.unlink(missing_ok=True)


@app.post("/sites", dependencies=protected)
async def site_save(request: Request):
    form = await request.form()
    f = {k: v for k, v in form.items() if isinstance(v, str)}
    upload = form.get("image_file")
    new_image = ""
    if upload is not None and not isinstance(upload, str) and upload.filename:
        try:
            new_image = await save_image(upload, request)
        except ValueError as e:
            return back("/sites", str(e))
    with m.session() as s:
        site = s.get(m.Site, int(f["id"])) if f.get("id") else None
        site = _site_from_form(f, site)
        if new_image or f.get("remove_image"):
            delete_image(site.image_url or "")
            site.image_url = new_image
        s.add(site)
        s.commit()
    return back("/sites", "ذخیره شد")


@app.post("/sites/import", dependencies=protected)
async def sites_import(request: Request):
    f = await request.form()
    text = f.get("text") or ""
    if f.get("file") and hasattr(f["file"], "read"):
        text = (await f["file"].read()).decode("utf-8-sig") or text
    n, errors = importer.import_sites(str(text))
    return back("/sites", f"{n} سایت اضافه شد" + (f" — خطاها: {'; '.join(errors[:10])}" if errors else ""))


@app.post("/sites/{site_id}/delete", dependencies=protected)
def site_delete(site_id: int):
    with m.session() as s:
        if s.exec(select(m.Campaign).where(m.Campaign.site_id == site_id)).first():
            return back("/sites", "اول کمپین‌های این سایت را حذف کن")
        for mp in s.exec(select(m.MediaPost).where(m.MediaPost.site_id == site_id)).all():
            delete_image(mp.image_url)
            s.delete(mp)
        s.delete(s.get(m.Site, site_id))
        s.commit()
    return back("/sites", "حذف شد")


# ---------------------------------------------------------------- one site: summary / instagram / publications / posts

PLATFORM_NAMES = {
    "instagram": "اینستاگرام", "telegram": "تلگرام", "x": "X (توییتر)", "linkedin": "لینکدین", "facebook": "فیسبوک",
    "threads": "Threads", "pinterest": "پینترست", "reddit": "ردیت", "mastodon": "ماستودون", "bluesky": "Bluesky",
    "telegraph": "Telegraph", "blogger": "Blogger", "wordpress": "وردپرس", "medium": "Medium", "tumblr": "Tumblr",
    "devto": "dev.to", "hashnode": "Hashnode", "ghost": "Ghost", "writeas": "Write.as", "webhook": "Webhook",
}


@app.get("/sites/{site_id}", response_class=HTMLResponse, dependencies=protected)
def site_page(request: Request, site_id: int, tab: str = "summary"):
    site = _get(m.Site, site_id)
    with m.session() as s:
        pubs = s.exec(select(m.Publication).where(m.Publication.site_id == site_id)
                      .order_by(m.Publication.created_at.desc())).all()
        posts = s.exec(select(m.MediaPost).where(m.MediaPost.site_id == site_id).order_by(m.MediaPost.id.desc())).all()
        campaigns = s.exec(select(m.Campaign).where(m.Campaign.site_id == site_id)).all()
    ok = [p for p in pubs if p.status == "ok"]
    today = dt.datetime.now(engine.tz()).date()

    def local_day(p: m.Publication) -> dt.date:
        return p.created_at.replace(tzinfo=dt.timezone.utc).astimezone(engine.tz()).date()

    days = [today - dt.timedelta(days=i) for i in range(13, -1, -1)]
    per_day = {d: sum(1 for p in ok if local_day(p) == d) for d in days}
    # one row (and one tab) per platform; "instagram" and "instagram_web" count as the same platform
    platforms: dict[str, dict[str, Any]] = {}
    for p in pubs:
        key = stats.base_kind(p.account_kind)
        row = platforms.setdefault(key, {"name": PLATFORM_NAMES.get(key, key), "pubs": [], "ok": 0, "error": 0,
                                         "dry_run": 0, "last": None, "views": 0, "likes": 0, "comments": 0,
                                         "shares": 0, "live": 0, "has_stats": stats.has_stats(key)})
        row["pubs"].append(p)
        row[p.status] = row.get(p.status, 0) + 1
        for k in ("views", "likes", "comments", "shares"):
            row[k] += getattr(p, k) or 0
        row["live"] += bool(p.link_found)
        if p.status == "ok" and (row["last"] is None or p.created_at > row["last"]):
            row["last"] = p.created_at
    tabs = {"summary": "خلاصه و آمار", **{k: f"{v['name']} ({v['ok']})" for k, v in platforms.items()},
            "posts": f"پست‌های آماده ({len(posts)})"}
    tab = tab if tab in tabs else "summary"
    summary = {
        "ok": len(ok), "errors": sum(1 for p in pubs if p.status == "error"),
        "today": per_day[today], "week": sum(per_day[d] for d in days[-7:]),
        "live": sum(1 for p in ok if p.link_found),
        **{k: sum(getattr(p, k) or 0 for p in ok) for k in ("views", "likes", "comments", "shares")},
    }
    return render(request, "site.html", site=site, tab=tab, tabs=tabs, platforms=platforms,
                  current=platforms.get(tab), posts=posts, campaigns=campaigns, summary=summary,
                  per_day=per_day, max_day=max(per_day.values()) or 1, sites={site.id: site})


@app.post("/sites/{site_id}/stats", dependencies=protected)
def site_stats_refresh(site_id: int, tab: str = "summary"):
    n = stats.refresh_all(max_age_hours=0, site_id=site_id)
    return back(f"/sites/{site_id}?tab={tab}", f"آمار {n} انتشار به‌روز شد")


# ---------------------------------------------------------------- ready posts (image + caption) per site


@app.get("/posts", response_class=HTMLResponse, dependencies=protected)
def posts_page(request: Request, site: int = 0, edit: int = 0):
    with m.session() as s:
        sites = s.exec(select(m.Site).order_by(m.Site.id)).all()
        q = select(m.MediaPost).order_by(m.MediaPost.site_id, m.MediaPost.id.desc())
        if site:
            q = q.where(m.MediaPost.site_id == site)
        posts = s.exec(q).all()
    current = _get(m.MediaPost, edit) if edit else None
    return render(request, "posts.html", posts=posts, sites=sites, names={x.id: x.name for x in sites},
                  f_site=site, current=current)


@app.post("/posts", dependencies=protected)
async def post_save(request: Request):
    form = await request.form()
    f = {k: v for k, v in form.items() if isinstance(v, str)}
    upload = form.get("image_file")
    new_image = ""
    if upload is not None and not isinstance(upload, str) and upload.filename:
        try:
            new_image = await save_image(upload, request)
        except ValueError as e:
            return back("/posts", str(e))
    with m.session() as s:
        mp = s.get(m.MediaPost, int(f["id"])) if f.get("id") else m.MediaPost(site_id=0)
        if not mp.id and not new_image:
            return back("/posts", "یک تصویر انتخاب کن")
        if new_image:
            delete_image(mp.image_url)
            mp.image_url = new_image
        mp.site_id = int(f.get("site_id") or 0)
        mp.caption = f.get("caption", "").strip()
        mp.link_url = f.get("link_url", "").strip()
        mp.enabled = bool(f.get("enabled"))
        if not s.get(m.Site, mp.site_id):
            return back("/posts", "سایت را انتخاب کن")
        s.add(mp)
        s.commit()
    return back("/posts", "پست ذخیره شد")


@app.post("/posts/{post_id}/{action}", dependencies=protected)
def post_action(post_id: int, action: str):
    with m.session() as s:
        mp = s.get(m.MediaPost, post_id)
        if mp is None:
            raise HTTPException(404)
        if action == "delete":
            delete_image(mp.image_url)
            s.delete(mp)
        elif action == "toggle":
            mp.enabled = not mp.enabled
            s.add(mp)
        else:
            raise HTTPException(400)
        s.commit()
    return back("/posts", "انجام شد")


# ---------------------------------------------------------------- accounts


@app.get("/accounts", response_class=HTMLResponse, dependencies=protected)
def accounts_page(request: Request, tag: str = "", kind: str = "", edit: int = 0):
    with m.session() as s:
        q = select(m.Account).order_by(m.Account.kind, m.Account.id)
        if tag:
            q = q.where(m.Account.tag == tag)
        if kind:
            q = q.where(m.Account.kind == kind)
        accounts = s.exec(q).all()
        tags = sorted({a.tag for a in s.exec(select(m.Account)).all() if a.tag})
        sites = s.exec(select(m.Site)).all()
    rows = [{"a": a, "creds": masked(a.creds), "web": a.kind.endswith("_web"), "session": browser.has_session(a.id),
             "shot": a.kind.endswith("_web") and browser.error_shot(a.id).exists(),
             "last": a.kind.endswith("_web") and browser.last_shot(a.id).exists()}
            for a in accounts]
    current = _get(m.Account, edit) if edit else None
    # token-based kinds that have a "log in as a user" (_web) replacement are hidden from the form;
    # accounts already using them keep working and stay selectable
    used = {a.kind for a in accounts} | ({current.kind} if current else set())
    kinds = {k: {"category": REGISTRY[k].category, "fields": KIND_FIELDS.get(k, []), "guide": KIND_GUIDES.get(k, {})}
             for k in sorted(REGISTRY) if k in used or k not in HIDDEN_KINDS}
    # Prefill non-secret values only; secret fields stay empty (= keep the stored value).
    current_creds = {k: v for k, v in current.creds.items() if not is_secret(k)} if current else {}
    return render(request, "accounts.html", rows=rows, kinds=kinds, tags=tags, sites=sites, f_tag=tag, f_kind=kind,
                  current=current, current_creds=current_creds)


@app.post("/accounts", dependencies=protected)
async def account_save(request: Request):
    f = dict(await request.form())
    kind = f.get("kind", "")
    if kind not in REGISTRY:
        return back("/accounts", "نوع پلتفرم نامعتبر است")
    with m.session() as s:
        acc = s.get(m.Account, int(f["id"])) if f.get("id") else m.Account(label="", kind=kind)
        creds = acc.creds if acc.id else {}
        for name in field_names(kind):
            v = str(f.get(f"cred_{name}", "")).strip()
            if v:
                creds[name] = v  # empty field on edit = keep the stored secret
        missing = missing_fields(kind, creds)
        if missing:
            return back("/accounts", f"فیلدهای الزامی خالی است: {', '.join(missing)}")
        acc.kind, acc.category = kind, REGISTRY[kind].category
        acc.label = f.get("label", "").strip() or f"{kind}-{acc.id or 'new'}"
        acc.tag = f.get("tag", "").strip()
        acc.min_hours_between = float(f.get("min_hours_between") or 12)
        acc.daily_limit = int(f.get("daily_limit") or 3)
        acc.notes = f.get("notes", "")
        acc.creds = creds
        s.add(acc)
        s.commit()
    return back("/accounts", "ذخیره شد")


# ---------------------------------------------------------------- log in as a user (live browser view)


def _web_account(account_id: int) -> m.Account:
    acc = _get(m.Account, account_id)
    if not acc.kind.endswith("_web"):
        raise HTTPException(400, "not a browser account")
    return acc


@app.get("/accounts/{account_id}/login", response_class=HTMLResponse, dependencies=protected)
def account_login_page(request: Request, account_id: int, restart: int = 0):
    acc = _web_account(account_id)
    if restart or not browser.login_active(acc.id):
        try:
            browser.login_start(acc.id, make(acc.kind, {**acc.creds, "_account_id": acc.id}).login_url)
        except Exception as e:  # noqa: BLE001
            return back("/accounts", f"مرورگر باز نشد: {e}")
    return render(request, "browser_login.html", acc=acc, viewport=browser.VIEWPORT)


@app.get("/accounts/{account_id}/login/shot", dependencies=protected)
def account_login_shot(account_id: int):
    try:
        img = browser.login_shot(account_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(410, str(e)) from e
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/accounts/{account_id}/login/act", dependencies=protected)
async def account_login_act(account_id: int, request: Request):
    action = await request.json()
    try:
        return {"ok": True, "url": browser.login_act(account_id, action)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e).splitlines()[0][:300]}


@app.post("/accounts/{account_id}/login/finish", dependencies=protected)
def account_login_finish(account_id: int):
    try:
        browser.login_finish(account_id)
    except Exception as e:  # noqa: BLE001
        return back(f"/accounts/{account_id}/login", f"ذخیره نشد: {e}")
    with m.session() as s:
        acc = s.get(m.Account, account_id)
        if acc:
            acc.status, acc.fail_count, acc.last_error = "ok", 0, ""
            s.add(acc)
            s.commit()
    return back("/accounts", "ورود ذخیره شد ✓ حالا «تست» را بزن")


@app.get("/accounts/{account_id}/{which}-shot", dependencies=protected)
def account_shot(account_id: int, which: str):
    if which not in ("error", "last"):
        raise HTTPException(404)
    f = browser.error_shot(account_id) if which == "error" else browser.last_shot(account_id)
    if not f.exists():
        raise HTTPException(404)
    return Response(f.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/accounts/{account_id}/login/cancel", dependencies=protected)
def account_login_cancel(account_id: int):
    browser.login_cancel(account_id)
    return back("/accounts", "پنجره‌ی ورود بسته شد")


@app.post("/accounts/import", dependencies=protected)
async def accounts_import(request: Request):
    f = await request.form()
    text = f.get("text") or ""
    if f.get("file") and hasattr(f["file"], "read"):
        text = (await f["file"].read()).decode("utf-8-sig") or text
    n, errors = importer.import_accounts(str(text), default_tag=str(f.get("tag") or "").strip())
    return back("/accounts", f"{n} اکانت اضافه شد" + (f" — خطاها: {'; '.join(errors[:10])}" if errors else ""))


@app.post("/accounts/{account_id}/{action}", dependencies=protected)
async def account_action(account_id: int, action: str, request: Request):
    if action == "test":
        f = await request.form()
        rec = engine.test_account(account_id, int(f.get("site_id") or 0) or None, dry_run=bool(f.get("dry")))
        return back("/accounts", f"تست: {rec.status} {rec.url or rec.error}")
    with m.session() as s:
        acc = s.get(m.Account, account_id)
        if acc is None:
            raise HTTPException(404)
        if action == "delete":
            s.delete(acc)
            browser.forget(account_id)
        elif action == "toggle":
            acc.enabled = not acc.enabled
        elif action == "resume":
            acc.status, acc.fail_count, acc.last_error = "ok", 0, ""
        else:
            raise HTTPException(400)
        if action != "delete":
            s.add(acc)
        s.commit()
    return back("/accounts", "انجام شد")


# ---------------------------------------------------------------- campaigns


@app.get("/campaigns", response_class=HTMLResponse, dependencies=protected)
def campaigns_page(request: Request, edit: int = 0):
    with m.session() as s:
        campaigns = s.exec(select(m.Campaign)).all()
        sites = s.exec(select(m.Site)).all()
        accounts = s.exec(select(m.Account).order_by(m.Account.kind)).all()
        tags = sorted({a.tag for a in accounts if a.tag})
    current = next((c for c in campaigns if c.id == edit), None)
    return render(request, "campaigns.html", campaigns=campaigns, sites={x.id: x for x in sites},
                  accounts=accounts, tags=tags, kinds=sorted(REGISTRY), current=current,
                  weekdays=["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"])


def _date(v: str) -> dt.date | None:
    return dt.date.fromisoformat(v) if v else None


@app.post("/campaigns", dependencies=protected)
async def campaign_save(request: Request):
    form = await request.form()
    f = dict(form)
    lo, hi = int(f.get("interval_min_minutes") or 120), int(f.get("interval_max_minutes") or 300)
    data = dict(
        name=f["name"].strip(), site_id=int(f["site_id"]), enabled=bool(f.get("enabled")),
        content_mode=f.get("content_mode", "both"), article_ratio=float(f.get("article_ratio") or 0.4),
        account_ids=[int(x) for x in form.getlist("account_ids")], account_tag=f.get("account_tag", "").strip(),
        kinds=list(form.getlist("kinds")),
        interval_min_minutes=min(lo, hi), interval_max_minutes=max(lo, hi),
        active_hours_start=int(f.get("active_hours_start") or 0), active_hours_end=int(f.get("active_hours_end") or 0),
        days_of_week=[int(x) for x in form.getlist("days_of_week")] or list(range(7)),
        start_date=_date(f.get("start_date", "")), end_date=_date(f.get("end_date", "")),
        daily_limit=int(f.get("daily_limit") or 4), total_limit=int(f.get("total_limit") or 0),
        extra_instructions=f.get("extra_instructions", ""),
    )
    with m.session() as s:
        c = s.get(m.Campaign, int(f["id"])) if f.get("id") else m.Campaign(name="", site_id=data["site_id"])
        for k, v in data.items():
            setattr(c, k, v)
        if c.next_run_at is None:
            c.next_run_at = m.utcnow()
        s.add(c)
        s.commit()
    return back("/campaigns", "ذخیره شد")


@app.post("/campaigns/{campaign_id}/{action}", dependencies=protected)
def campaign_action(campaign_id: int, action: str):
    if action in ("run", "preview"):
        rec = engine.run_campaign(campaign_id, force=True, dry_run=True if action == "preview" else None)
        msg = f"{rec.status}: {rec.url or rec.error or rec.title}" if rec else "اکانت آماده‌ای پیدا نشد (cooldown/سهمیه)"
        return back("/campaigns", msg)
    with m.session() as s:
        c = s.get(m.Campaign, campaign_id)
        if c is None:
            raise HTTPException(404)
        if action == "delete":
            s.delete(c)
        elif action == "toggle":
            c.enabled = not c.enabled
            s.add(c)
        else:
            raise HTTPException(400)
        s.commit()
    return back("/campaigns", "انجام شد")


# ---------------------------------------------------------------- publications + JSON API


@app.get("/publications", response_class=HTMLResponse, dependencies=protected)
def publications_page(request: Request, site_id: int = 0, status: str = "", limit: int = 300):
    with m.session() as s:
        q = select(m.Publication).order_by(m.Publication.created_at.desc()).limit(limit)
        if site_id:
            q = q.where(m.Publication.site_id == site_id)
        if status:
            q = q.where(m.Publication.status == status)
        pubs = s.exec(q).all()
        sites = {x.id: x for x in s.exec(select(m.Site)).all()}
    return render(request, "publications.html", pubs=pubs, sites=sites, f_site=site_id, f_status=status)


@app.post("/publications/{pub_id}/resend", dependencies=protected)
def publication_resend(pub_id: int, request: Request):
    try:
        new = engine.republish(pub_id)
    except Exception as e:  # noqa: BLE001
        return back("/publications", f"ارسال دوباره نشد: {e}")
    nxt = request.headers.get("referer", "/publications")
    path = "/" + nxt.split("://", 1)[-1].split("/", 1)[-1].split("?")[0] if "://" in nxt else "/publications"
    return back(path, f"ارسال دوباره: {new.status} {new.url or new.error}")


@app.get("/api/publications", dependencies=protected)
def api_publications(limit: int = 200):
    with m.session() as s:
        rows = s.exec(select(m.Publication).order_by(m.Publication.created_at.desc()).limit(limit)).all()
    return [r.model_dump() for r in rows]


@app.get("/api/sites", dependencies=protected)
def api_sites():
    with m.session() as s:
        return [x.model_dump() for x in s.exec(select(m.Site)).all()]


@app.get("/api/accounts", dependencies=protected)
def api_accounts():
    with m.session() as s:
        return [{**a.model_dump(exclude={"creds_enc"}), "creds": masked(a.creds)} for a in s.exec(select(m.Account)).all()]


@app.get("/api/campaigns", dependencies=protected)
def api_campaigns():
    with m.session() as s:
        return [c.model_dump() for c in s.exec(select(m.Campaign)).all()]


@app.post("/api/accounts/import", dependencies=protected)
async def api_accounts_import(request: Request, tag: str = ""):
    n, errors = importer.import_accounts((await request.body()).decode("utf-8-sig"), default_tag=tag)
    return {"imported": n, "errors": errors}


@app.post("/api/sites/import", dependencies=protected)
async def api_sites_import(request: Request):
    n, errors = importer.import_sites((await request.body()).decode("utf-8-sig"))
    return {"imported": n, "errors": errors}


@app.get("/api/kinds", dependencies=protected)
def api_kinds():
    return {k: {"category": REGISTRY[k].category,
                "fields": [{"name": n, "required": r, "label": lb, "help": h} for n, r, lb, h in KIND_FIELDS.get(k, [])],
                "guide": KIND_GUIDES.get(k, {})}
            for k in sorted(REGISTRY)}
