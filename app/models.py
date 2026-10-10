"""Database models: sites, accounts (encrypted credentials), campaigns (schedules), publications, logs."""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pydantic import NaiveDatetime
from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine, select

from . import crypto
from .config import settings


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _json(default: Any) -> Any:
    return Field(default_factory=lambda: type(default)(default), sa_column=Column(JSON))


class Site(SQLModel, table=True):
    """One of your websites that should receive backlinks."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    name: str
    url: str
    language: str = "fa"
    niche: str = ""
    description: str = ""
    keywords: list = _json([])
    anchors: list = _json([])  # extra anchor texts
    pages: list = _json([])  # [{"url": ..., "keywords": [...]}] deep pages that also deserve links
    image_url: str = ""  # used by Instagram / Pinterest
    style: str = ""  # tone / style guidance appended to prompts
    enabled: bool = True
    # this site's own Google Analytics 4 (clicks are sent there, the goal is created there)
    ga_measurement_id: str = ""
    ga_api_secret: str = ""
    ga_property_id: str = ""


class MediaPost(SQLModel, table=True):
    """A ready post for a site: an image plus (optionally) your own caption and link. A site can have
    many; social posts use them in rotation (least used first). Empty caption = the AI writes the text."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    site_id: int = Field(index=True)
    image_url: str = ""
    caption: str = ""
    link_url: str = ""  # empty = a page of the site chosen as usual
    enabled: bool = True
    used_count: int = 0
    last_used_at: Optional[NaiveDatetime] = None


def pick_media(s: Session, site_id: int) -> "MediaPost | None":
    """The ready post of this site that was used the least (and longest ago)."""
    rows = s.exec(select(MediaPost).where(MediaPost.site_id == site_id, MediaPost.enabled == True)).all()  # noqa: E712
    return min(rows, key=lambda r: (r.used_count, r.last_used_at or dt.datetime.min, r.id), default=None)


class Account(SQLModel, table=True):
    """A publishing account on one platform. `kind` selects the publisher adapter."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    label: str
    kind: str = Field(index=True)
    category: str = "social"  # article | social (derived from the adapter)
    tag: str = Field(default="", index=True)  # free grouping label, campaigns can select by tag
    creds_enc: str = ""  # encrypted JSON of adapter options
    enabled: bool = True
    status: str = "ok"  # ok | paused (auto-paused after repeated failures)
    fail_count: int = 0
    last_error: str = ""
    last_used_at: Optional[NaiveDatetime] = None
    published_count: int = 0
    min_hours_between: float = 12.0  # cooldown between two posts from this account
    daily_limit: int = 3  # max posts per day from this account (all campaigns together)
    notes: str = ""

    @property
    def creds(self) -> dict[str, Any]:
        return crypto.decrypt(self.creds_enc)

    @creds.setter
    def creds(self, value: dict[str, Any]) -> None:
        self.creds_enc = crypto.encrypt(value)

    @property
    def usable(self) -> bool:
        return self.enabled and self.status == "ok"


class Campaign(SQLModel, table=True):
    """Schedule: publish AI content for one site through a set of accounts at random intervals."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    name: str
    site_id: int = Field(foreign_key="site.id", index=True)
    enabled: bool = True
    content_mode: str = "both"  # both | article | social
    article_ratio: float = 0.4  # in "both" mode, share of runs that produce an article
    # Which accounts: explicit ids, and/or every account with this tag, optionally limited to kinds.
    # All empty = every usable account.
    account_ids: list = _json([])
    account_tag: str = ""
    kinds: list = _json([])
    # Timing: next run happens a random number of minutes in [interval_min, interval_max] later.
    interval_min_minutes: int = 120
    interval_max_minutes: int = 300
    active_hours_start: int = 8  # local time, inclusive
    active_hours_end: int = 23  # local time, exclusive
    days_of_week: list = _json([0, 1, 2, 3, 4, 5, 6])  # 0 = Monday
    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    daily_limit: int = 4
    total_limit: int = 0  # 0 = unlimited
    extra_instructions: str = ""
    next_run_at: Optional[NaiveDatetime] = None
    last_run_at: Optional[NaiveDatetime] = None


class Publication(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    campaign_id: Optional[int] = Field(default=None, index=True)
    site_id: int = Field(index=True)
    account_id: int = Field(index=True)
    account_kind: str = ""
    account_label: str = ""
    category: str = "article"  # article | social
    title: str = ""
    link_url: str = ""  # the backlink we placed
    anchor: str = ""
    url: str = ""  # where it was published
    external_id: str = ""
    status: str = "ok"  # ok | error | dry_run
    error: str = ""
    checked_at: Optional[NaiveDatetime] = None
    link_found: Optional[bool] = None
    link_rel: str = ""  # "" (dofollow) | nofollow | ugc | sponsored ...
    body_preview: str = ""
    image_url: str = ""  # image posted with a social post
    track_code: str = Field(default="", index=True)  # /r/<code> short link and utm_content
    clicks: int = 0  # clicks on the short link (counted by this dashboard)
    views: Optional[int] = None  # stats, refreshed periodically (app/stats.py); None = not available
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    stats_at: Optional[NaiveDatetime] = None


class Click(SQLModel, table=True):
    """One click on a tracked short link (/r/<code>)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    publication_id: int = Field(index=True)
    campaign_id: Optional[int] = Field(default=None, index=True)
    site_id: int = Field(default=0, index=True)
    platform: str = ""
    referer: str = ""


class RunLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    level: str = "info"
    message: str = ""


_engine = None


def engine():
    global _engine
    if _engine is None:
        from . import runtime  # noqa: F401 — registers the settings table
        _engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
        _add_missing_columns(_engine)
    return _engine


def _add_missing_columns(eng: Any) -> None:
    """Tiny migration: columns added to a model after the database was created are added to the table
    (existing rows get NULL / the column default). Nothing is ever dropped or changed."""
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=eng.dialect)
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                extra = ""
                if isinstance(default, bool):
                    extra = f" DEFAULT {int(default)}"
                elif isinstance(default, (int, float)):
                    extra = f" DEFAULT {default}"
                elif isinstance(default, str):
                    extra = " DEFAULT '" + default.replace("'", "''") + "'"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{extra}'))


def reset_engine() -> None:
    global _engine
    _engine = None


def session() -> Session:
    return Session(engine(), expire_on_commit=False)


def log_run(level: str, message: str) -> None:
    with session() as s:
        s.add(RunLog(level=level, message=message[:2000]))
        s.commit()


def day_start_utc(tz: dt.tzinfo, now: dt.datetime | None = None) -> dt.datetime:
    local = (now.replace(tzinfo=dt.timezone.utc).astimezone(tz) if now else dt.datetime.now(tz))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(dt.timezone.utc).replace(tzinfo=None)


def count_since(s: Session, since: dt.datetime, **where: Any) -> int:
    q = select(Publication).where(Publication.created_at >= since, Publication.status != "error")
    for k, v in where.items():
        q = q.where(getattr(Publication, k) == v)
    return len(s.exec(q).all())


def recent_titles(s: Session, site_id: int, limit: int = 30) -> list[str]:
    rows = s.exec(
        select(Publication.title).where(Publication.site_id == site_id, Publication.category == "article")
        .order_by(Publication.created_at.desc()).limit(limit)
    ).all()
    return [r for r in rows if r]
