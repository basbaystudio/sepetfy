"""Uygulama önyüklemesi: ayarlar + loglama + veritabanı."""

from __future__ import annotations

import logging

from sepetfy.app.logging import setup_logging
from sepetfy.config import Settings, get_settings
from sepetfy.database import create_schema, init_engine

log = logging.getLogger(__name__)


def bootstrap(settings: Settings | None = None, *, create_tables: bool = True) -> Settings:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_dir, settings.secret_values())
    init_engine(settings.database_url)
    if create_tables:
        create_schema()
    if settings.publishing_enabled and not settings.admin_user_ids:
        log.error("PUBLISHING_ENABLED=true fakat TELEGRAM_ADMIN_USER_IDS boş: hiçbir yayın onaylanamaz")
    return settings
