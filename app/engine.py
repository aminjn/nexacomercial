"""Campaign engine: which campaign is due, which account posts, generate, publish, record, reschedule."""
from __future__ import annotations

import datetime as dt
import logging
import random
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from . import models as m
from .config import settings
from .content import generate_article, generate_social
from .linkcheck import check_backlink
from .llm import get_llm
from .publishers import PublishError, make

log = logging.getLogger(__name__)


def tz() -> dt.tzinfo:
    try:
        return ZoneInfo(settings.timezone)
    except Exception:  # noqa: BLE001
        return dt.timezone.utc


def _local(now_utc: dt.datetime) -> dt.datetime:
    return now_utc.replace(tzinfo=dt.timezone.utc).astimezone(tz())


# ---------------------------------------------------------------- schedule rules


def in_window(c: m.Campaign, now_utc: dt.datetime | None = None) -> bool:
    """Is `now` inside the campaign's date range, weekdays and active hours (local time)?"""
    local = _local(now_utc or m.utcnow())
    if c.start_date and local.date() < c.start_date:
        return False
    if c.end_date and local.date() > c.end_date:
        return False
    if c.days_of_week and local.weekday() not in c.days_of_week:
        return False
    a, b = c.active_hours_start, c.active_hours_end
    if a == b:
        return True
    return a <= local.hour < b if a < b else (local.hour >= a or local.hour < b)  # window may wrap midnight


def next_run(c: m.Campaign, now_utc: dt.datetime | None = None) -> dt.datetime:
    lo = max(1, c.interval_min_minutes)
    hi = max(lo, c.interval_max_minutes)
    return (now_utc or m.utcnow()) + dt.timedelta(minutes=random.uniform(lo, hi))


def campaign_accounts(s: Session, c: m.Campaign, category: str | None = None) -> list[m.Account]:
    """Usable accounts selected by the campaign (ids / tag / kinds), optionally of one category."""
    accs = [a for a in s.exec(select(m.Account)).all() if a.usable]
    ids, tag, kinds = set(c.account_ids or []), c.account_tag.strip(), set(c.kinds or [])
    if ids or tag:
        accs = [a for a in accs if a.id in ids or (tag and a.tag == tag)]
    if kinds:
        accs = [a for a in accs if a.kind in kinds]
    if category:
        accs = [a for a in accs if a.category == category]
    return accs


def account_ready(s: Session, a: m.Account, now_utc: dt.datetime) -> bool:
    if a.last_used_at and now_utc - a.last_used_at < dt.timedelta(hours=a.min_hours_between):
        return False
    return m.count_since(s, m.day_start_utc(tz(), now_utc), account_id=a.id) < a.daily_limit


def pick_account(s: Session, accounts: list[m.Account], now_utc: dt.datetime) -> m.Account | None:
    """Least-recently-used ready account (random among never-used ones)."""
    ready = [a for a in accounts if account_ready(s, a, now_utc)]
    if not ready:
        return None
    never = [a for a in ready if a.last_used_at is None]
    return random.choice(never) if never else min(ready, key=lambda a: a.last_used_at)


def category_order(c: m.Campaign) -> list[str]:
    if c.content_mode in ("article", "social"):
        return [c.content_mode]
    first = "article" if random.random() < c.article_ratio else "social"
    return [first, "social" if first == "article" else "article"]


# ---------------------------------------------------------------- publishing


def publish(site: m.Site, account: m.Account, *, campaign: m.Campaign | None = None,
            dry_run: bool | None = None) -> m.Publication:
    """Generate one article / social post for `site` and publish it through `account`."""
    dry = settings.dry_run if dry_run is None else dry_run
    extra = campaign.extra_instructions if campaign else ""
    rec = m.Publication(campaign_id=campaign.id if campaign else None, site_id=site.id, account_id=account.id,
                        account_kind=account.kind, account_label=account.label, category=account.category)
    stage = "setup"  # setup → ai (writing) → publish (platform API); only publish errors count against the account
    try:
        creds = account.creds
        pub = make(account.kind, {**creds, "_account_id": account.id})
        stage = "ai"
        llm = get_llm()
        if pub.category == "article":
            with m.session() as s:
                recent = m.recent_titles(s, site.id)
            art = generate_article(llm, site, recent, extra)
            rec.title, rec.link_url, rec.anchor = art.title, art.link_url, art.anchor
            rec.body_preview = art.body_markdown[:600]
            stage = "publish"
            res = None if dry else pub.publish_article(art)
        else:
            post = generate_social(llm, site, account.kind.removesuffix("_web"), max_chars=min(pub.max_chars or 240, 240), extra=extra)
            rec.title, rec.link_url = post.text[:120], post.link_url
            rec.body_preview = post.render()[:600]
            if pub.needs_image and not (post.image_url or creds.get("image_url")):
                stage = "setup"
                raise PublishError(f"{account.kind} بدون تصویر پست نمی‌گذارد؛ در صفحه‌ی «سایت‌ها» برای این سایت تصویر انتخاب کن")
            stage = "publish"
            res = None if dry else pub.publish_social(post)
        if dry:
            rec.status = "dry_run"
        else:
            rec.url, rec.external_id = res.url, res.external_id
    except Exception as e:  # noqa: BLE001 — record every failure, never crash the scheduler
        rec.status, rec.error = "error", explain_error(stage, account.kind, e)[:1000]
        log.exception("[site %s] %s stage failed for account %s", site.id, stage, account.id)

    with m.session() as s:
        acc = s.get(m.Account, account.id)
        if rec.status == "error" and stage == "publish":
            acc.fail_count += 1
            acc.last_error = rec.error
            if acc.fail_count >= settings.account_max_failures:
                acc.status = "paused"
                s.add(m.RunLog(level="warning",
                               message=f"account #{acc.id} {acc.label} auto-paused after {acc.fail_count} failures"))
        elif rec.status == "error":
            acc.last_error = rec.error  # AI / setup problem: not the account's fault, don't pause it
        elif rec.status == "ok":
            acc.fail_count, acc.last_error = 0, ""
            acc.last_used_at = m.utcnow()
            acc.published_count += 1
        s.add(acc)
        s.add(rec)
        s.add(m.RunLog(level="error" if rec.status == "error" else "info",
                       message=f"{site.name} → {acc.label} ({acc.kind}) [{rec.category}] {rec.status} "
                               f"{rec.url or rec.error}"[:2000]))
        s.commit()
        s.refresh(rec)
    return rec


STAGE_NAMES = {"setup": "تنظیمات", "ai": "هوش مصنوعی (نوشتن محتوا)", "publish": "انتشار در"}


def explain_error(stage: str, kind: str, e: Exception) -> str:
    """Human-readable (Persian) error: which stage failed, why, and what to do."""
    raw = f"{type(e).__name__}: {e}"
    low = raw.lower()
    where = f"{STAGE_NAMES['publish']} {kind}" if stage == "publish" else STAGE_NAMES.get(stage, stage)
    if "name resolution" in low or "name or service not known" in low or "nodename nor servname" in low:
        why = "آدرس سرور پیدا نشد (DNS)."
    elif "connection refused" in low:
        why = "سرور جواب نداد (پورت بسته است یا سرویس خاموش است)."
    elif "timed out" in low or "timeout" in low:
        why = "زمان انتظار تمام شد؛ سرور خیلی کند است یا اتصال برقرار نمی‌شود."
    elif "http 401" in low or "http 403" in low or " 401" in low or " 403" in low:
        why = "توکن/کلید نامعتبر یا منقضی است، یا دسترسی لازم را ندارد."
    elif "connecterror" in low or "connection" in low:
        why = "اتصال برقرار نشد."
    else:
        why = ""
    if stage == "ai":
        todo = "در صفحه‌ی «تنظیمات» آدرس سرور و مدل هوش مصنوعی را چک کن و «تست اتصال» را بزن."
    elif stage == "publish" and ("connect" in low or "timeout" in low or "timed out" in low or "name resolution" in low):
        todo = "اگر این پلتفرم در ایران فیلتر است، در صفحه‌ی «تنظیمات» پراکسی انتشار را بگذار."
    elif stage == "publish" and why.startswith("توکن"):
        todo = "در صفحه‌ی اکانت‌ها اطلاعات این اکانت را ویرایش کن."
    else:
        todo = ""
    return " ".join(x for x in (f"[{where}]", why, todo, f"— {raw}") if x)


def run_campaign(campaign_id: int, *, force: bool = False, dry_run: bool | None = None) -> m.Publication | None:
    """Run one step of a campaign: pick an account, publish one item, schedule the next run."""
    now = m.utcnow()
    with m.session() as s:
        c = s.get(m.Campaign, campaign_id)
        if c is None:
            raise KeyError(campaign_id)
        site = s.get(m.Site, c.site_id)
        reason = ""
        if not force:
            if not (c.enabled and site and site.enabled):
                reason = "disabled"
            elif not in_window(c, now):
                reason = "outside window"
            elif m.count_since(s, m.day_start_utc(tz(), now), campaign_id=c.id) >= c.daily_limit:
                reason = "daily limit reached"
            elif c.total_limit and m.count_since(s, dt.datetime.min, campaign_id=c.id) >= c.total_limit:
                reason = "total limit reached"
        account = None
        if not reason:
            for cat in category_order(c):
                account = pick_account(s, campaign_accounts(s, c, cat), now)
                if account:
                    break
            if account is None:
                reason = "no ready account"
        c.next_run_at = next_run(c, now)
        s.add(c)
        s.commit()
    if reason:
        log.info("campaign %s skipped: %s", campaign_id, reason)
        return None
    rec = publish(site, account, campaign=c, dry_run=dry_run)
    with m.session() as s:
        c = s.get(m.Campaign, campaign_id)
        c.last_run_at = m.utcnow()
        s.add(c)
        s.commit()
    return rec


def due_campaigns(now_utc: dt.datetime | None = None) -> list[int]:
    now = now_utc or m.utcnow()
    with m.session() as s:
        rows = s.exec(select(m.Campaign).where(m.Campaign.enabled == True)).all()  # noqa: E712
    due = [c for c in rows if c.next_run_at is None or c.next_run_at <= now]
    due = [c for c in due if in_window(c, now)]
    due.sort(key=lambda c: c.next_run_at or dt.datetime.min)
    return [c.id for c in due]


def tick(*, dry_run: bool | None = None) -> list[m.Publication]:
    """Scheduler entry point: run every due campaign (bounded per tick)."""
    out: list[m.Publication] = []
    for cid in due_campaigns()[: settings.max_jobs_per_tick]:
        try:
            rec = run_campaign(cid, dry_run=dry_run)
            if rec:
                out.append(rec)
        except Exception:  # noqa: BLE001
            log.exception("campaign %s failed", cid)
    return out


def test_account(account_id: int, site_id: int | None = None, *, dry_run: bool = False) -> m.Publication:
    """Publish one item through an account (real by default) to verify its credentials."""
    with m.session() as s:
        acc = s.get(m.Account, account_id)
        if acc is None:
            raise KeyError(account_id)
        site = s.get(m.Site, site_id) if site_id else s.exec(select(m.Site).where(m.Site.enabled == True)).first()  # noqa: E712
    if site is None:
        raise PublishError("add a site first")
    return publish(site, acc, dry_run=dry_run)


def verify_links(max_age_hours: float | None = None) -> int:
    """Re-check every published item's backlink (found? dofollow?)."""
    max_age_hours = settings.linkcheck_hours if max_age_hours is None else max_age_hours
    cutoff = m.utcnow() - dt.timedelta(hours=max_age_hours)
    n = 0
    with m.session() as s:
        rows = s.exec(select(m.Publication).where(m.Publication.status == "ok", m.Publication.url != "")).all()
        for p in rows:
            if p.checked_at and p.checked_at > cutoff:
                continue
            p.link_found, p.link_rel = check_backlink(p.url, p.link_url)
            p.checked_at = m.utcnow()
            s.add(p)
            n += 1
        s.commit()
    return n
