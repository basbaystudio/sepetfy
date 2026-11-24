"""Uygulama önyüklemesi: ayarlar + loglama + veritabanı."""

from __future__ import annotations

import logging

from sepetfy.app.logging import setup_logging
from sepetfy.config import Settings, get_settings
from sepetfy.database import create_schema, init_engine

log = logging.getLogger(__name__)


def _on_breaker_open(service: str, cooldown: float) -> None:
    from sepetfy.notifications.telegram import alert

    alert(f"breaker:{service}", f"{service} art arda hata verdi; {cooldown / 60:.0f} dk boyunca "
                                "çağrı yapılmayacak (sistem güvenli modda devam ediyor).", cooldown_hours=1)


def _setup_notifications(settings: Settings) -> None:
    from sepetfy.app.resilience import add_open_listener
    from sepetfy.notifications.telegram import Notifier, configure_notifier

    configure_notifier(Notifier(settings) if settings.telegram_bot_token is not None else None)
    add_open_listener(_on_breaker_open)


def bootstrap(settings: Settings | None = None, *, create_tables: bool = True) -> Settings:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_dir, settings.secret_values())
    init_engine(settings.database_url)
    if create_tables:
        create_schema()
    _setup_notifications(settings)
    if settings.publishing_enabled and not settings.admin_user_ids:
        log.error("PUBLISHING_ENABLED=true fakat TELEGRAM_ADMIN_USER_IDS boş: hiçbir yayın onaylanamaz")
    return settings
