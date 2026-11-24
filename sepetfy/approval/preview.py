"""Onay önizlemesi: fırsat kartını yöneticiye Telegram'dan gönderir.

Kart: ürün görseli, ad, mağaza, fiyatlar, doğrulanmış indirim, uyarılar, bağlantı, platformlar
ve butonlar (platform seçimi + ONAYLA / REDDET / DÜZENLE / SONRA). Hazırlanan içeriklerin
tam metinleri kartın ardından ayrı mesajlar olarak gelir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from sepetfy.app.runtime import delete_key, get_json, set_json
from sepetfy.approval.guard import DEMO_PREFIX, compute_content_hash
from sepetfy.content.telegram_post import build_telegram_post, tl
from sepetfy.database.models import (
    ContentItem, ContentStatus, Deal, DealStatus, Platform, PriceSnapshot, as_utc, utcnow,
)
from sepetfy.notifications.telegram import Notifier

log = logging.getLogger(__name__)

PLATFORM_LABELS = {
    Platform.TELEGRAM: "Telegram", Platform.INSTAGRAM: "Instagram", Platform.TIKTOK: "TikTok",
    Platform.WHATSAPP: "WhatsApp", Platform.PINTEREST: "Pinterest", Platform.X: "X",
}
REMIND_AFTER = timedelta(hours=3)


def sel_key(deal_id: int) -> str:
    return f"sel:{deal_id}"


def later_key(deal_id: int) -> str:
    return f"later:{deal_id}"


def pending_contents(deal: Deal) -> list[ContentItem]:
    return [c for c in deal.contents if c.status == ContentStatus.PENDING_APPROVAL]


def ensure_template_content(session: Session, deal: Deal) -> list[ContentItem]:
    """AI gerektirmeyen içerikleri (şimdilik Telegram gönderisi) hazırlar; varsa tekrar üretmez."""
    if any(c.platform == Platform.TELEGRAM for c in deal.contents):
        return deal.contents
    snap = session.get(PriceSnapshot, deal.snapshot_id)
    body = build_telegram_post(deal.product, snap, deal)
    item = ContentItem(deal_id=deal.id, platform=Platform.TELEGRAM, kind="post", body=body,
                       content_hash=compute_content_hash(body, None, Platform.TELEGRAM.value),
                       status=ContentStatus.DRAFT)
    session.add(item)
    session.flush()
    session.refresh(deal)
    deal.status = DealStatus.CONTENT_READY
    return deal.contents


def keyboard(deal: Deal, selected: list[str]) -> dict:
    toggles = []
    for c in pending_contents(deal):
        p = Platform(c.platform)
        mark = "☑" if p.value in selected else "☐"
        toggles.append({"text": f"{mark} {PLATFORM_LABELS[p]}", "callback_data": f"t:{deal.id}:{p.value}"})
    rows = [toggles] if toggles else []
    rows.append([{"text": "✅ ONAYLA", "callback_data": f"A:{deal.id}"},
                 {"text": "❌ REDDET", "callback_data": f"R:{deal.id}"}])
    rows.append([{"text": "✏️ DÜZENLE", "callback_data": f"E:{deal.id}"},
                 {"text": "⏰ SONRA", "callback_data": f"L:{deal.id}"}])
    return {"inline_keyboard": rows}


def result_keyboard(label: str) -> dict:
    return {"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]}


def preview_card(session: Session, deal: Deal) -> str:
    p = deal.product
    snap = session.get(PriceSnapshot, deal.snapshot_id)
    flags = deal.score_breakdown or {}
    demo = p.external_id.startswith(DEMO_PREFIX)
    score = f"{deal.score}/100 ({deal.grade.value})" if deal.score is not None else "henüz yok (FAZ 3)"
    lines = [
        ("🧪 <b>DEMO — yayınlanamaz</b>\n" if demo else "") + f"🔔 <b>Fırsat #{deal.id}</b> · Puan: {score}",
        f"<b>{escape(p.title)}</b>",
        f"🏬 {escape(p.store.name)}" + (f" · {escape(p.brand)}" if p.brand else ""),
        f"💸 Şimdi: <b>{tl(snap.price)}</b>",
    ]
    if deal.discount_verified:
        lines.append(f"✅ Doğrulanmış indirim: <b>%{deal.verified_discount_pct:g}</b> "
                     f"(30 günün en düşüğü: {tl(deal.reference_price)})")
    else:
        lines.append("⚠️ İndirim doğrulanmadı")
    if flags.get("claimed_discount_pct"):
        lines.append(f"🏷 Mağazanın iddiası: %{flags['claimed_discount_pct']:g} (eski fiyat {tl(snap.list_price)})")
    if flags.get("inflated_list_price"):
        lines.append("🚩 <b>Şüpheli eski fiyat</b>: mağazanın eski fiyatı geçmişte görülmedi")
    stock = {True: "Stokta", False: "Stokta YOK", None: "Stok bilinmiyor"}[snap.in_stock]
    lines.append(f"📦 {stock}" + (f" · 🎟 {escape(snap.coupon_code)}" if snap.coupon_code else ""))
    lines.append(f"🔗 {escape(p.affiliate_url or p.url)}")
    platforms = ", ".join(PLATFORM_LABELS[Platform(c.platform)] for c in pending_contents(deal)) or "-"
    lines.append(f"📣 Hazırlanan içerikler: {platforms}")
    return "\n".join(lines)


def send_deal_preview(session: Session, notifier: Notifier, deal: Deal) -> bool:
    ensure_template_content(session, deal)
    for c in deal.contents:
        if c.status in (ContentStatus.DRAFT, ContentStatus.PENDING_APPROVAL):
            c.status = ContentStatus.PENDING_APPROVAL
    deal.status = DealStatus.PENDING_APPROVAL
    session.flush()

    selected = get_json(session, sel_key(deal.id))
    if selected is None:
        selected = [Platform(c.platform).value for c in pending_contents(deal)]
        set_json(session, sel_key(deal.id), selected)

    sent = notifier.send(preview_card(session, deal), reply_markup=keyboard(deal, selected),
                         photo=deal.product.image_url)
    for c in pending_contents(deal):
        label = PLATFORM_LABELS[Platform(c.platform)]
        notifier.send(f"📝 <b>{label} içeriği</b> (fırsat #{deal.id}):\n\n{c.body}")
    delete_key(session, later_key(deal.id))
    return bool(sent)


def preview_new_deals(session: Session, notifier: Notifier, deal_ids: list[int] | None = None,
                      limit: int = 5) -> int:
    """Yeni fırsatlardan en yüksek doğrulanmış indirimlileri (en fazla ``limit``) onaya sunar."""
    q = select(Deal).where(Deal.status.in_([DealStatus.NEW, DealStatus.CONTENT_READY]))
    if deal_ids is not None:
        if not deal_ids:
            return 0
        q = q.where(Deal.id.in_(deal_ids))
    deals = session.scalars(q.order_by(Deal.verified_discount_pct.desc(), Deal.id).limit(limit)).all()
    count = 0
    for deal in deals:
        if session.get(PriceSnapshot, deal.snapshot_id).in_stock is False:
            continue
        if send_deal_preview(session, notifier, deal):
            count += 1
    return count


def schedule_reminder(session: Session, deal_id: int, now: datetime | None = None) -> datetime:
    when = (now or utcnow()) + REMIND_AFTER
    set_json(session, later_key(deal_id), when.isoformat())
    return when


def due_reminders(session: Session, now: datetime | None = None) -> list[int]:
    from sepetfy.database.models import AppSetting

    now = now or utcnow()
    due = []
    for row in session.scalars(select(AppSetting).where(AppSetting.key.like("later:%"))):
        when = as_utc(datetime.fromisoformat(get_json(session, row.key)))
        if when <= now:
            due.append(int(row.key.split(":", 1)[1]))
    return due
