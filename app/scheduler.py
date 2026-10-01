"""APScheduler wiring: frequent campaign ticks + periodic backlink verification."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import engine
from .config import settings

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler:
        return _scheduler
    sch = BackgroundScheduler(timezone=settings.timezone)
    sch.add_job(engine.tick, "interval", seconds=settings.tick_seconds, id="tick", coalesce=True, max_instances=1)
    sch.add_job(engine.verify_links, "interval", hours=max(1, settings.linkcheck_hours // 2), id="linkcheck",
                coalesce=True, max_instances=1)
    sch.start()
    _scheduler = sch
    log.info("scheduler started: tick every %ss (%s)", settings.tick_seconds, settings.timezone)
    return sch


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
