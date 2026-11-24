"""Çalışma zamanı durumu: acil durdurma şalteri ve küçük JSON ayarları (``app_settings`` tablosu).

Acil durdurma (``/durdur``) açıkken:
- hiçbir yayıncı yayın yapamaz (``BasePublisher.publish``),
- hiçbir ücretli AI çağrısı yapılamaz (``analytics.costs.ensure_budget``).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from sepetfy.database.models import AppSetting

HALT_KEY = "system_halted"


def get_json(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(AppSetting, key)
    if row is None:
        return default
    try:
        return json.loads(row.value)
    except ValueError:
        return default


def set_json(session: Session, key: str, value: Any) -> None:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=raw))
    else:
        row.value = raw
    session.flush()


def delete_key(session: Session, key: str) -> None:
    row = session.get(AppSetting, key)
    if row is not None:
        session.delete(row)
        session.flush()


def is_halted(session: Session) -> bool:
    state = get_json(session, HALT_KEY, {})
    return bool(state.get("halted")) if isinstance(state, dict) else False


def set_halted(session: Session, halted: bool, actor_id: int | None = None) -> None:
    from sepetfy.database.models import utcnow

    set_json(session, HALT_KEY, {"halted": halted, "by": actor_id, "at": utcnow().isoformat()})
