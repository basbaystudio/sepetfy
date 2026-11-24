"""Fiyat geçmişi analizi ve indirim doğrulama.

Referans fiyat kuralı: **indirimden önceki 30 gün içindeki en düşük fiyat**.
(Türkiye'de indirimli satış duyurularında esas alınan yaklaşımla uyumludur ve
"önce fiyatı şişir, sonra indirim yap" hilesini otomatik olarak etkisiz kılar.)

- İndirim yalnızca kendi gözlemlerimize dayanır; mağazanın "eski fiyat"ı (``list_price``)
  sadece karşılaştırma ve sahte indirim tespiti için kullanılır.
- Ürünü en az ``min_history_days`` gündür izlemiyorsak indirim "doğrulanmadı" sayılır.
- Mağazanın gösterdiği eski fiyat, son 30 günde gördüğümüz en yüksek fiyattan belirgin
  biçimde yüksekse "şişirilmiş eski fiyat" işareti konur.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sepetfy.database.models import Deal, DealStatus, PriceSnapshot, as_utc

WINDOW_DAYS = 30
MIN_HISTORY_DAYS = 7
MIN_DROP_PCT = 10.0
INFLATION_TOLERANCE = Decimal("1.05")


@dataclass
class PriceAnalysis:
    current_price: Decimal
    reference_price: Decimal | None = None      # son 30 günün en düşüğü (bizim gözlemimiz)
    max_price_30d: Decimal | None = None
    history_days: float = 0.0
    verified: bool = False
    drop_pct: float | None = None               # referansa göre doğrulanmış düşüş
    claimed_discount_pct: float | None = None   # mağazanın iddia ettiği indirim
    inflated_list_price: bool = False
    in_stock: bool | None = None
    reasons: list[str] = field(default_factory=list)


def _pct(old: Decimal, new: Decimal) -> float:
    return float((old - new) / old * 100) if old > 0 else 0.0


def analyze_snapshot(
    session: Session,
    snapshot: PriceSnapshot,
    *,
    window_days: int = WINDOW_DAYS,
    min_history_days: int = MIN_HISTORY_DAYS,
) -> PriceAnalysis:
    now = as_utc(snapshot.observed_at)
    prior = session.scalars(
        select(PriceSnapshot)
        .where(PriceSnapshot.product_id == snapshot.product_id, PriceSnapshot.id < snapshot.id)
        .order_by(PriceSnapshot.observed_at, PriceSnapshot.id)
    ).all()

    a = PriceAnalysis(current_price=snapshot.price, in_stock=snapshot.in_stock)
    if snapshot.list_price and snapshot.list_price > snapshot.price:
        a.claimed_discount_pct = round(_pct(snapshot.list_price, snapshot.price), 1)

    if not prior:
        a.reasons.append("İlk gözlem: fiyat geçmişi yok, indirim doğrulanamaz")
        return a

    a.history_days = (now - as_utc(prior[0].observed_at)).total_seconds() / 86400
    window_start = now - timedelta(days=window_days)
    # Pencere içindeki gözlemler + pencere başında hâlâ geçerli olan son fiyat
    in_window = [s for s in prior if as_utc(s.observed_at) >= window_start]
    before = [s for s in prior if as_utc(s.observed_at) < window_start]
    if before:
        in_window.insert(0, before[-1])
    prices = [s.price for s in in_window]
    a.reference_price = min(prices)
    a.max_price_30d = max(prices)
    a.drop_pct = round(_pct(a.reference_price, snapshot.price), 1)
    a.verified = a.history_days >= min_history_days

    if not a.verified:
        a.reasons.append(f"Fiyat geçmişi yetersiz ({a.history_days:.1f} gün < {min_history_days} gün)")
    elif a.drop_pct > 0:
        a.reasons.append(f"Son {window_days} günün en düşük fiyatına göre %{a.drop_pct} düşüş "
                         f"({a.reference_price} -> {snapshot.price})")
    else:
        a.reasons.append(f"Son {window_days} günün en düşük fiyatının ({a.reference_price}) altında değil")

    if (a.verified and snapshot.list_price is not None
            and snapshot.list_price > a.max_price_30d * INFLATION_TOLERANCE):
        a.inflated_list_price = True
        a.reasons.append(f"Şüpheli eski fiyat: mağaza {snapshot.list_price} gösteriyor, "
                         f"son {window_days} günde gördüğümüz en yüksek fiyat {a.max_price_30d}")
    if snapshot.in_stock is False:
        a.reasons.append("Stokta yok")
    return a


def deal_fingerprint(snapshot: PriceSnapshot, reference: Decimal | None) -> str:
    raw = f"{snapshot.product_id}|{snapshot.price}|{snapshot.coupon_code or ''}|{reference}"
    return hashlib.sha256(raw.encode()).hexdigest()


def maybe_create_deal(
    session: Session, snapshot: PriceSnapshot, analysis: PriceAnalysis, *, min_drop_pct: float = MIN_DROP_PCT
) -> Deal | None:
    """Doğrulanmış, stokta olan ve eşiği geçen düşüşler için fırsat adayı oluşturur.

    Aynı ürün + fiyat + kupon + referans için ikinci kez aday oluşturmaz (tekrar analiz önlenir).
    Puanlama ve A/B/C sınıflandırma FAZ 3'te yapılır.
    """
    if not analysis.verified or analysis.in_stock is False:
        return None
    if analysis.drop_pct is None or analysis.drop_pct < min_drop_pct:
        return None
    fp = deal_fingerprint(snapshot, analysis.reference_price)
    if session.scalar(select(Deal.id).where(Deal.fingerprint == fp)) is not None:
        return None
    deal = Deal(
        product_id=snapshot.product_id,
        snapshot_id=snapshot.id,
        fingerprint=fp,
        reference_price=analysis.reference_price,
        verified_discount_pct=analysis.drop_pct,
        discount_verified=True,
        reasons=analysis.reasons,
        score_breakdown={"inflated_list_price": analysis.inflated_list_price,
                         "claimed_discount_pct": analysis.claimed_discount_pct},
        status=DealStatus.NEW,
    )
    session.add(deal)
    session.flush()
    return deal


def price_series(session: Session, product_id: int) -> list[tuple[datetime, Decimal]]:
    rows = session.execute(
        select(PriceSnapshot.observed_at, PriceSnapshot.price)
        .where(PriceSnapshot.product_id == product_id).order_by(PriceSnapshot.observed_at)
    ).all()
    return [(as_utc(t), p) for t, p in rows]
