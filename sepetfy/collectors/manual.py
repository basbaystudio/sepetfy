"""Manuel ürün gözlemi: yönetici Telegram'dan link + ad + fiyat gönderir.

Sepetfy mağaza sayfalarını otomatik okumaz (kullanım şartları). Fiyatı siz girersiniz;
aynı linki her gönderdiğinizde fiyat geçmişine yeni bir gözlem eklenir ve indirim,
feed'lerdeki kurallarla (son 30 günün en düşüğü, 7 gün asgari geçmiş) doğrulanır.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from sepetfy.app.errors import SepetfyError
from sepetfy.database.models import Deal, PriceSnapshot, Product, Store, utcnow
from sepetfy.price_tracker.history import PriceAnalysis, analyze_snapshot, maybe_create_deal

DOMAIN_STORES = {
    "hepsiburada.com": "hepsiburada",
    "trendyol.com": "trendyol",
    "amazon.com.tr": "amazon_tr",
    "n11.com": "n11",
    "mediamarkt.com.tr": "mediamarkt",
    "teknosa.com": "teknosa",
    "boyner.com.tr": "boyner",
    "zara.com": "zara",
}


class InvalidLink(SepetfyError):
    pass


def normalize_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        raise InvalidLink("Geçerli bir http(s) bağlantısı değil")
    return urlunsplit((parts.scheme, parts.hostname.lower(), parts.path.rstrip("/") or "/", "", ""))


def store_code_for(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for domain, code in DOMAIN_STORES.items():
        if host == domain or host.endswith("." + domain):
            return code
    return "manual"


@dataclass
class ManualResult:
    product: Product
    snapshot: PriceSnapshot
    analysis: PriceAnalysis
    observations: int
    deal: Deal | None


def add_manual_observation(
    session: Session, url: str, title: str, price: Decimal, list_price: Decimal | None = None,
    *, now: datetime | None = None, external_prefix: str = "url:",
) -> ManualResult:
    now = now or utcnow()
    clean = normalize_url(url)
    store = session.scalar(select(Store).where(Store.code == store_code_for(clean)))
    if store is None:
        store = session.scalar(select(Store).where(Store.code == "manual"))
    if store is None:
        raise SepetfyError("Mağaza kataloğu yüklenmemiş (init-db)")

    ext_id = external_prefix + hashlib.sha1(clean.encode()).hexdigest()[:20]
    product = session.scalar(select(Product).where(Product.store_id == store.id, Product.external_id == ext_id))
    if product is None:
        product = Product(store_id=store.id, external_id=ext_id, title=title[:512], url=url.strip())
        session.add(product)
    elif title:
        product.title = title[:512]
    product.last_checked_at = now
    product.active = True
    product.missing_count = 0

    snap = PriceSnapshot(product=product, price=price, list_price=list_price, currency="TRY",
                         in_stock=True, source="manual", observed_at=now)
    session.add(snap)
    session.flush()
    analysis = analyze_snapshot(session, snap)
    deal = maybe_create_deal(session, snap, analysis)
    count = len(session.scalars(select(PriceSnapshot.id).where(PriceSnapshot.product_id == product.id)).all())
    return ManualResult(product, snap, analysis, count, deal)
