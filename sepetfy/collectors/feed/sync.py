"""Feed ürünlerini veritabanıyla eşitler.

- Yeni ürün -> eklenir.
- Fiyat / eski fiyat / stok / kupon / kargo değiştiyse -> yeni ``PriceSnapshot`` (fiyat geçmişi).
  Değişmediyse snapshot yazılmaz (veritabanı gereksiz büyümez); ``last_checked_at`` güncellenir.
- Feed'de olmayan ürün -> ``missing_count`` artar; art arda N kez yoksa pasife alınır.
- Ürünler partiler hâlinde işlenir; 200 bin+ ürünlük feed'ler de sabit bellekle çalışır.
- Fiyatı düşen ürünler fiyat geçmişi analizine gönderilir; doğrulanmış düşüşler fırsat adayı olur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import islice
from typing import Iterable, Iterator

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from sepetfy.collectors.feed.parser import FeedItem
from sepetfy.database.models import PriceSnapshot, Product, Store
from sepetfy.price_tracker.history import analyze_snapshot, maybe_create_deal

_PRODUCT_FIELDS = ("title", "brand", "category", "url", "affiliate_url", "image_url",
                   "seller_name", "seller_rating", "rating", "review_count")


@dataclass
class SyncStats:
    total: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    price_drops: int = 0
    deals_created: int = 0
    deactivated: int = 0
    deal_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("deal_ids")
        return d


def _snapshot_differs(last: PriceSnapshot, it: FeedItem) -> bool:
    return (
        last.price != it.price
        or last.list_price != it.list_price
        or last.in_stock != it.in_stock
        or (last.coupon_code or None) != (it.coupon_code or None)
        or last.shipping_cost != it.shipping_cost
        or last.currency != it.currency
    )


def _latest_snapshots(session: Session, product_ids: list[int]) -> dict[int, PriceSnapshot]:
    if not product_ids:
        return {}
    latest_ids = (
        select(func.max(PriceSnapshot.id))
        .where(PriceSnapshot.product_id.in_(product_ids))
        .group_by(PriceSnapshot.product_id)
    )
    rows = session.scalars(select(PriceSnapshot).where(PriceSnapshot.id.in_(latest_ids))).all()
    return {s.product_id: s for s in rows}


def _batched(items: Iterable[FeedItem], n: int) -> Iterator[list[FeedItem]]:
    it = iter(items)
    while chunk := list(islice(it, n)):
        yield chunk


def sync_items(
    session: Session,
    store: Store,
    items: Iterable[FeedItem],
    *,
    source: str,
    now: datetime,
    missing_runs_to_deactivate: int = 3,
    detect_deals: bool = True,
    batch_size: int = 2000,
) -> SyncStats:
    """Ürünleri partiler hâlinde eşitler; bellek kullanımı feed boyutundan bağımsızdır."""
    stats = SyncStats()
    drop_snapshot_ids: list[int] = []

    for chunk in _batched(items, batch_size):
        stats.total += len(chunk)
        products = {p.external_id: p for p in session.scalars(
            select(Product).where(Product.store_id == store.id,
                                  Product.external_id.in_([it.external_id for it in chunk])))}
        latest = _latest_snapshots(session, [p.id for p in products.values()])
        touched: list = []
        drops: list[PriceSnapshot] = []

        for it in chunk:
            product = products.get(it.external_id)
            if product is None:
                product = Product(store_id=store.id, external_id=it.external_id)
                session.add(product)
                stats.new += 1
            touched.append(product)
            for name in _PRODUCT_FIELDS:
                val = getattr(it, name)
                if val is not None and getattr(product, name) != val:
                    setattr(product, name, val)
            product.last_checked_at = now
            product.missing_count = 0
            product.active = True

            last = latest.get(product.id) if product.id is not None else None
            if last is not None and not _snapshot_differs(last, it):
                stats.unchanged += 1
                continue
            snap = PriceSnapshot(
                product=product, price=it.price, list_price=it.list_price, currency=it.currency,
                shipping_cost=it.shipping_cost, coupon_code=it.coupon_code, coupon_verified=False,
                in_stock=it.in_stock, source=source, observed_at=now,
            )
            session.add(snap)
            touched.append(snap)
            if last is not None:
                stats.changed += 1
                if it.price < last.price:
                    stats.price_drops += 1
                    drops.append(snap)

        session.flush()
        drop_snapshot_ids.extend(s.id for s in drops)
        # Bellek: işlenen partinin nesnelerini oturumdan çıkar
        for obj in touched:
            session.expunge(obj)
        for obj in latest.values():
            if obj in session:
                session.expunge(obj)

    # Bu çalıştırmada görülmeyen (last_checked_at < now) aktif ürünler
    not_seen = and_(Product.store_id == store.id, Product.active.is_(True),
                    or_(Product.last_checked_at.is_(None), Product.last_checked_at < now))
    session.execute(update(Product).where(not_seen)
                    .values(missing_count=Product.missing_count + 1)
                    .execution_options(synchronize_session=False))
    stats.deactivated = session.execute(
        update(Product)
        .where(Product.store_id == store.id, Product.active.is_(True),
               Product.missing_count >= missing_runs_to_deactivate)
        .values(active=False).execution_options(synchronize_session=False)
    ).rowcount or 0

    store.last_checked_at = now
    session.flush()

    if detect_deals:
        for snap_id in drop_snapshot_ids:
            snap = session.get(PriceSnapshot, snap_id)
            deal = maybe_create_deal(session, snap, analyze_snapshot(session, snap))
            if deal is not None:
                stats.deals_created += 1
                stats.deal_ids.append(deal.id)
    return stats
