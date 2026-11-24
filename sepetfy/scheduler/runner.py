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


def build_scheduler(settings: Settings) -> BlockingScheduler:
    sched = BlockingScheduler(timezone=settings.timezone,
                              job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    sched.add_job(safe_job(heartbeat), IntervalTrigger(minutes=1), args=[settings], id="heartbeat")
    sched.add_job(safe_job(daily_backup), CronTrigger(hour=4, minute=17), args=[settings], id="daily_backup")
    return sched


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
