"""Kaynak kataloğunu ``stores`` tablosuna yazar (idempotent).

Tüm kaynaklar ``enabled=False`` olarak eklenir; erişim doğrulanınca açılır.
Mevcut kayıtlardaki ``enabled`` değeri korunur.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from sepetfy.config.sources import SOURCES
from sepetfy.database.models import Store


def seed_stores(session: Session) -> int:
    added = 0
    for spec in SOURCES:
        store = session.scalar(select(Store).where(Store.code == spec.code))
        if store is None:
            store = Store(code=spec.code, enabled=False)
            session.add(store)
            added += 1
        store.name = spec.name
        store.base_url = spec.base_url
        store.access_method = spec.access_method
        store.access_status = spec.access_status
        store.check_interval_min = spec.min_interval_min or 0
        store.notes = spec.notes
    return added
