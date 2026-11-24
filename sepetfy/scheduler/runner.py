"""Zamanlayıcı servisi (7/24 çalışan süreç).

FAZ 1'de yalnızca altyapı görevleri vardır: heartbeat ve günlük yedekleme.
Kaynak kontrolleri (FAZ 2), raporlar (FAZ 7) buraya eklenecek.
Her görev hatası yakalanır ve loglanır; bir görevin çökmesi servisi durdurmaz.
"""

from __future__ import annotations

import functools
import logging
import signal
from pathlib import Path
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from sepetfy.config import Settings
from sepetfy.database import ping
from sepetfy.database.backup import backup_database

log = logging.getLogger(__name__)

HEARTBEAT_FILE_NAME = "heartbeat"


def safe_job(fn: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            log.exception("Görev hatası: %s", fn.__name__)
    return wrapper


def heartbeat(settings: Settings) -> None:
    ping()
    (settings.log_dir / HEARTBEAT_FILE_NAME).write_text("ok")
    log.debug("heartbeat ok")


def daily_backup(settings: Settings) -> None:
    backup_database(settings.database_url, settings.backup_dir, settings.backup_keep)


def feed_job(settings: Settings, feed_name: str) -> None:
    from sepetfy.collectors.feed import load_feed_configs, run_feed
    from sepetfy.database import session_scope

    cfg = load_feed_configs()[feed_name]
    with session_scope() as session:
        report = run_feed(session, cfg, settings.data_dir)
    if report.status in ("failed", "rejected"):
        log.error("Feed %s: %s — %s", feed_name, report.status, report.message)
    elif report.deal_ids:
        log.info("Feed %s: %d yeni fırsat adayı", feed_name, len(report.deal_ids))


def stale_feed_check(settings: Settings) -> None:
    from sepetfy.collectors.feed import load_feed_configs, stale_feeds
    from sepetfy.database import session_scope

    with session_scope() as session:
        stale = stale_feeds(session, load_feed_configs())
    for name in stale:
        # FAZ 5: Telegram uyarısı olarak da gönderilecek
        log.warning("Feed beklenen sürede güncellenmedi: %s", name)


def build_scheduler(settings: Settings) -> BlockingScheduler:
    from sepetfy.collectors.feed import load_feed_configs

    sched = BlockingScheduler(timezone=settings.timezone,
                              job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    sched.add_job(safe_job(heartbeat), IntervalTrigger(minutes=1), args=[settings], id="heartbeat")
    sched.add_job(safe_job(daily_backup), CronTrigger(hour=4, minute=17), args=[settings], id="daily_backup")
    feeds = [c for c in load_feed_configs().values() if c.enabled]
    for cfg in feeds:
        # URL tanımlıysa ilk kontrol hemen; değilse normal aralıkta (her seferinde .env yeniden okunur)
        extra = {"next_run_time": _soon()} if cfg.resolve_url() else {}
        sched.add_job(safe_job(feed_job), IntervalTrigger(minutes=cfg.interval_min), args=[settings, cfg.name],
                      id=f"feed:{cfg.name}", **extra)
        log.info("Feed zamanlandı: %s (her %d dk)", cfg.name, cfg.interval_min)
    if feeds:
        sched.add_job(safe_job(stale_feed_check), IntervalTrigger(hours=1), args=[settings], id="stale_feeds")
    return sched


def _soon():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(seconds=20)


def run(settings: Settings) -> None:
    sched = build_scheduler(settings)

    def _stop(signum, _frame):
        log.info("Sinyal %s alındı, zamanlayıcı durduruluyor", signum)
        sched.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("Sepetfy zamanlayıcı başladı (DRY_RUN=%s, PUBLISHING_ENABLED=%s)",
             settings.dry_run, settings.publishing_enabled)
    heartbeat(settings)
    sched.start()


def heartbeat_age_seconds(log_dir: Path) -> float | None:
    import time
    f = log_dir / HEARTBEAT_FILE_NAME
    return time.time() - f.stat().st_mtime if f.exists() else None
