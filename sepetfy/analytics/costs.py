"""Maliyet defteri ve bütçe koruması.

Her ücretli çağrıdan ÖNCE ``ensure_budget(tahmini_maliyet)`` çağrılır; sonra gerçek
maliyet ``record_cost`` ile yazılır. Bütçe sınırı aşılacaksa ``BudgetExceeded``
fırlatılır (``BUDGET_HARD_STOP=true`` iken). Aylık bütçe ``app_settings`` tablosundan
(Telegram'dan değiştirilebilir) veya .env'den okunur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sepetfy.app.errors import BudgetExceeded, SystemHalted
from sepetfy.config import Settings
from sepetfy.database.models import AppSetting, CostEntry

log = logging.getLogger(__name__)

BUDGET_KEY = "monthly_budget_usd"


@dataclass
class BudgetStatus:
    budget_usd: float
    spent_usd: float
    ratio: float
    warn: bool
    exceeded: bool


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def record_cost(session: Session, service: str, operation: str, cost_usd: float | Decimal,
                units: float = 0, unit: str = "", ref_type: str | None = None,
                ref_id: int | None = None, meta: dict | None = None) -> CostEntry:
    entry = CostEntry(service=service, operation=operation, cost_usd=Decimal(str(cost_usd)),
                      units=units, unit=unit, ref_type=ref_type, ref_id=ref_id, meta=meta)
    session.add(entry)
    session.flush()
    return entry


def get_budget(session: Session, settings: Settings) -> float:
    row = session.get(AppSetting, BUDGET_KEY)
    return float(row.value) if row else settings.monthly_budget_usd


def set_budget(session: Session, value_usd: float) -> None:
    if value_usd < 0:
        raise ValueError("Bütçe negatif olamaz")
    row = session.get(AppSetting, BUDGET_KEY)
    if row is None:
        session.add(AppSetting(key=BUDGET_KEY, value=str(value_usd)))
    else:
        row.value = str(value_usd)
    session.flush()


def spent_since(session: Session, since: datetime, service: str | None = None) -> float:
    q = select(func.coalesce(func.sum(CostEntry.cost_usd), 0)).where(CostEntry.created_at >= since)
    if service:
        q = q.where(CostEntry.service == service)
    return float(session.scalar(q) or 0)


def budget_status(session: Session, settings: Settings, extra_usd: float = 0.0) -> BudgetStatus:
    budget = get_budget(session, settings)
    spent = spent_since(session, month_start()) + extra_usd
    ratio = spent / budget if budget > 0 else (float("inf") if spent > 0 else 0.0)
    return BudgetStatus(budget, spent, ratio, ratio >= settings.budget_warn_ratio, ratio > 1.0)


def ensure_budget(session: Session, settings: Settings, estimated_usd: float) -> BudgetStatus:
    """Her ücretli çağrıdan ÖNCE çağrılır."""
    from sepetfy.app.runtime import is_halted
    from sepetfy.notifications.telegram import alert

    if is_halted(session):
        raise SystemHalted("Acil durdurma aktif: ücretli çağrı yapılmadı (/baslat ile açılır)")
    st = budget_status(session, settings, extra_usd=estimated_usd)
    if st.exceeded and settings.budget_hard_stop:
        msg = f"Aylık bütçe aşılacak: {st.spent_usd:.2f}$ / {st.budget_usd:.2f}$ — ücretli çağrı engellendi"
        alert("budget_exceeded", msg + "\nYükseltmek için: /butce <USD>", cooldown_hours=12)
        raise BudgetExceeded(msg)
    if st.warn:
        alert("budget_warn", f"Aylık bütçenin %{st.ratio * 100:.0f}'i kullanıldı "
                             f"({st.spent_usd:.2f}$ / {st.budget_usd:.2f}$)", cooldown_hours=24)
    return st


def summary(session: Session, settings: Settings) -> dict:
    now = datetime.now(timezone.utc)
    ms = month_start(now)
    day = now - timedelta(days=1)
    by_service = dict(session.execute(
        select(CostEntry.service, func.sum(CostEntry.cost_usd))
        .where(CostEntry.created_at >= ms).group_by(CostEntry.service)
    ).all())
    spent_month = spent_since(session, ms)
    days_elapsed = max((now - ms).total_seconds() / 86400, 1 / 24)
    return {
        "budget_usd": get_budget(session, settings),
        "last_24h_usd": round(spent_since(session, day), 4),
        "month_to_date_usd": round(spent_month, 4),
        "projected_month_usd": round(spent_month / days_elapsed * 30, 2),
        "by_service": {k: round(float(v), 4) for k, v in by_service.items()},
    }
