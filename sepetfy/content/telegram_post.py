"""Telegram kanal gönderisi şablonu (AI gerektirmez, maliyeti 0).

Kurallar:
- Yalnızca DOĞRULANMIŞ indirim "indirim" olarak yazılır; referans = son 30 günün en düşük fiyatı.
- Mağazanın "eski fiyat"ı indirim iddiası olarak kullanılmaz.
- Affiliate bağlantısı açıkça belirtilir (reklam/işbirliği şeffaflığı).
- Fiyat ve stoğun değişebileceği uyarısı her gönderide bulunur.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape

from sepetfy.database.models import Deal, PriceSnapshot, Product

AFFILIATE_NOTE = "ℹ️ Bu bağlantı üzerinden yapılan alışverişlerden Sepetfy komisyon alabilir."
VOLATILITY_NOTE = "⏳ Fiyat ve stok mağaza tarafından her an değiştirilebilir."


def tl(value: Decimal | float | None) -> str:
    """1199.9 -> '1.199,90 TL'"""
    if value is None:
        return "-"
    s = f"{Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} TL"


def build_telegram_post(product: Product, snapshot: PriceSnapshot, deal: Deal) -> str:
    lines = [f"🔥 <b>{escape(product.title)}</b>"]
    if product.brand:
        lines.append(f"🏷 {escape(product.brand)}")
    if deal.discount_verified and deal.reference_price and deal.verified_discount_pct:
        lines.append(f"💸 <s>{tl(deal.reference_price)}</s> → <b>{tl(snapshot.price)}</b>  "
                     f"(%{deal.verified_discount_pct:g} indirim)")
        lines.append("✅ Son 30 günün en düşük fiyatına göre doğrulandı")
    else:
        lines.append(f"💸 <b>{tl(snapshot.price)}</b>")
    if snapshot.coupon_code:
        note = "" if snapshot.coupon_verified else " (kupon doğrulanmadı, sepette kontrol edin)"
        lines.append(f"🎟 Kupon: <code>{escape(snapshot.coupon_code)}</code>{note}")
    if snapshot.shipping_cost is not None:
        lines.append("🚚 Kargo: " + ("Ücretsiz" if snapshot.shipping_cost == 0 else tl(snapshot.shipping_cost)))
    link = product.affiliate_url or product.url
    lines += ["", f"🛒 {escape(link)}", "", VOLATILITY_NOTE, AFFILIATE_NOTE]
    return "\n".join(lines)
