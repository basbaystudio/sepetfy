"""Sepetfy Telegram yönetim botu.

GÜVENLİK
- Yalnızca TELEGRAM_ADMIN_USER_IDS içindeki kullanıcıların ÖZEL sohbetten gelen mesajları işlenir.
  Diğer herkes yok sayılır (yönetici tanımlı değilse sadece kurulum için kendi kimliği söylenir).
- Onay kararları ``approval.guard.record_decision`` ile kaydedilir (append-only, içerik özeti ile).
- Telegram güncellemeleri "en fazla bir kez" işlenir: konum (offset) işlemden ÖNCE kaydedilir,
  böylece çökme sonrası aynı buton ikinci kez işlenmez.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select

from sepetfy.analytics.costs import budget_status, get_budget, set_budget, summary
from sepetfy.app.errors import ExternalServiceError, PublishNotAllowed, SepetfyError
from sepetfy.app.runtime import delete_key, get_json, is_halted, set_halted, set_json
from sepetfy.approval.guard import DEMO_PREFIX, record_decision
from sepetfy.approval.preview import (
    PLATFORM_LABELS, keyboard, later_key, pending_contents, preview_new_deals, result_keyboard,
    schedule_reminder, sel_key, send_deal_preview,
)
from sepetfy.collectors.feed import import_feed_file, load_feed_configs, stale_feeds
from sepetfy.collectors.feed.fetcher import _decompress
from sepetfy.collectors.feed.parser import parse_price
from sepetfy.collectors.manual import InvalidLink, add_manual_observation, normalize_url, store_code_for
from sepetfy.config import Settings
from sepetfy.content.telegram_post import tl
from sepetfy.database import session_scope
from sepetfy.database.models import (
    ApprovalDecision, Deal, DealStatus, FeedState, Platform, Product, as_utc, utcnow,
)
from sepetfy.integrations.telegram_api import MAX_DOWNLOAD_BYTES, TelegramAPI, TelegramError
from sepetfy.notifications.telegram import Notifier

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+", re.I)
CONV_TTL = timedelta(minutes=30)
OFFSET_KEY = "telegram_offset"

COMMANDS: list[tuple[str, str]] = [
    ("durum", "Sistem durumu"),
    ("bekleyenler", "Onay bekleyen fırsatlar"),
    ("firsatlar", "Yeni fırsatları onaya gönder"),
    ("maliyet", "Harcama özeti"),
    ("butce", "Aylık bütçeyi gör / ayarla: /butce 15"),
    ("feeds", "Ürün feed'lerinin durumu"),
    ("durdur", "ACİL DURDURMA: tüm yayın ve ücretli çağrılar"),
    ("baslat", "Acil durdurmayı kaldır"),
    ("demo", "Deneme fırsatı gönder (yayınlanamaz)"),
    ("iptal", "Yarım kalan işlemi iptal et"),
    ("yardim", "Komutlar ve kullanım"),
]

HELP = (
    "🤖 <b>Sepetfy Yönetim Botu</b>\n\n"
    "<b>Ürün ekleme:</b> Ürün linkini gönderin; ad ve fiyatı soracağım. Tek mesajda da olur:\n"
    "<code>link\nürün adı\nfiyat\n(eski fiyat)</code>\n"
    "Aynı linki ileride yeni fiyatla gönderdikçe fiyat geçmişi oluşur.\n\n"
    "<b>XML feed:</b> .xml / .xml.gz / .zip dosyasını buraya gönderin (en fazla 20 MB).\n\n"
    "<b>Komutlar:</b>\n" + "\n".join(f"/{c} — {d}" for c, d in COMMANDS)
)


def _pct(v: float) -> str:
    return f"%{v * 100:.0f}"


class SepetfyBot:
    def __init__(self, settings: Settings, api: TelegramAPI, *, notifier: Notifier | None = None,
                 session_factory: Callable = session_scope):
        self.settings = settings
        self.api = api
        self.notifier = notifier or Notifier(settings, api)
        self.sessions = session_factory

    # ================================================================ giriş noktası
    def handle_update(self, upd: dict) -> None:
        chat_id = None
        try:
            if "callback_query" in upd:
                cq = upd["callback_query"]
                chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
                self._on_callback(cq)
            elif "message" in upd:
                chat_id = upd["message"].get("chat", {}).get("id")
                self._on_message(upd["message"])
        except Exception:
            log.exception("Telegram güncellemesi işlenemedi")
            if chat_id in self.settings.admin_user_ids:
                try:
                    self.reply(chat_id, "⚠️ Bir hata oluştu; ayrıntı loglara yazıldı.")
                except ExternalServiceError:
                    pass

    def reply(self, chat_id: int, text: str, markup: dict | None = None) -> None:
        self.api.send_message(chat_id, text, markup)

    def _authorized(self, user: dict, chat: dict) -> bool:
        return chat.get("type") == "private" and user.get("id") in self.settings.admin_user_ids

    # ================================================================ mesajlar
    def _on_message(self, msg: dict) -> None:
        chat, user = msg.get("chat", {}), msg.get("from", {})
        uid, chat_id = user.get("id"), chat.get("id")
        if not self._authorized(user, chat):
            if not self.settings.admin_user_ids and chat.get("type") == "private" and uid:
                self.reply(chat_id, "🔧 <b>Sepetfy kurulum modu</b>\n"
                                    f"Telegram kullanıcı kimliğiniz: <code>{uid}</code>\n"
                                    "Bunu sunucudaki .env dosyasında TELEGRAM_ADMIN_USER_IDS satırına yazıp "
                                    "servisi yeniden başlatın. O zamana kadar bot hiçbir komutu çalıştırmaz.")
            else:
                log.warning("Yetkisiz Telegram erişimi yok sayıldı")
            return

        if msg.get("document"):
            self._on_document(chat_id, uid, msg["document"])
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            self._on_command(chat_id, uid, text)
            return
        conv = self._get_conv(uid)
        if conv:
            self._on_conversation(chat_id, uid, conv, text)
            return
        if URL_RE.search(text):
            self._start_manual(chat_id, uid, text)
            return
        self.reply(chat_id, "Ürün eklemek için linkini gönderin. Komutlar için /yardim")

    # ---------------------------------------------------------------- konuşma durumu
    def _conv_key(self, uid: int) -> str:
        return f"conv:{uid}"

    def _get_conv(self, uid: int) -> dict | None:
        with self.sessions() as s:
            conv = get_json(s, self._conv_key(uid))
            if not conv:
                return None
            if as_utc(utcnow()) > as_utc(datetime.fromisoformat(conv["exp"])):
                delete_key(s, self._conv_key(uid))
                return None
            return conv

    def _set_conv(self, uid: int, conv: dict) -> None:
        conv["exp"] = (utcnow() + CONV_TTL).isoformat()
        with self.sessions() as s:
            set_json(s, self._conv_key(uid), conv)

    def _clear_conv(self, uid: int) -> None:
        with self.sessions() as s:
            delete_key(s, self._conv_key(uid))

    def _on_conversation(self, chat_id: int, uid: int, conv: dict, text: str) -> None:
        mode = conv.get("mode")
        if mode == "manual_title":
            if len(text) < 3:
                self.reply(chat_id, "Ürün adı çok kısa, tekrar yazın:")
                return
            conv.update(mode="manual_price", title=text)
            self._set_conv(uid, conv)
            self.reply(chat_id, "💸 Güncel fiyatı yazın (ör. <code>1.299,90</code>):")
        elif mode == "manual_price":
            price = parse_price(text)
            if price is None or price <= 0:
                self.reply(chat_id, "Fiyatı anlayamadım. Örnek: <code>1299,90</code>")
                return
            conv.update(mode="manual_list", price=str(price))
            self._set_conv(uid, conv)
            self.reply(chat_id, "🏷 Mağazanın gösterdiği eski (üstü çizili) fiyat? Yoksa <code>yok</code> yazın:")
        elif mode == "manual_list":
            list_price = None
            if text.lower() not in ("yok", "-", "hayır", "hayir", "no"):
                list_price = parse_price(text)
                if list_price is None:
                    self.reply(chat_id, "Anlayamadım. Fiyat yazın ya da <code>yok</code> deyin:")
                    return
            self._clear_conv(uid)
            self._save_manual(chat_id, conv["url"], conv["title"], Decimal(conv["price"]), list_price)
        elif mode == "edit_note":
            self._clear_conv(uid)
            self._record_edit(chat_id, uid, conv["deal"], text)
        else:
            self._clear_conv(uid)
            self.reply(chat_id, "Önceki işlem zaman aşımına uğradı. /yardim")

    # ================================================================ manuel ürün
    def _start_manual(self, chat_id: int, uid: int, text: str) -> None:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        url = URL_RE.search(text).group().rstrip(").,")
        try:
            normalize_url(url)
        except InvalidLink as exc:
            self.reply(chat_id, f"❌ {exc}")
            return
        if len(lines) >= 3 and URL_RE.match(lines[0]):
            price = parse_price(lines[2])
            if price and price > 0:
                list_price = parse_price(lines[3]) if len(lines) > 3 else None
                self._save_manual(chat_id, url, lines[1], price, list_price)
                return
        existing = self._find_manual(url)
        store = store_code_for(normalize_url(url))
        if existing:
            self._set_conv(uid, {"mode": "manual_price", "url": url, "title": existing})
            self.reply(chat_id, f"🔁 Bu ürünü zaten takip ediyoruz: <b>{escape(existing)}</b>\n"
                                "💸 Güncel fiyatı yazın:")
        else:
            self._set_conv(uid, {"mode": "manual_title", "url": url})
            self.reply(chat_id, f"🔗 Link alındı (mağaza: <b>{escape(store)}</b>).\n📝 Ürün adını yazın:")

    def _find_manual(self, url: str) -> str | None:
        ext = "url:" + hashlib.sha1(normalize_url(url).encode()).hexdigest()[:20]
        with self.sessions() as s:
            return s.scalar(select(Product.title).where(Product.external_id == ext))

    def _save_manual(self, chat_id: int, url: str, title: str, price: Decimal, list_price: Decimal | None) -> None:
        with self.sessions() as s:
            res = add_manual_observation(s, url, title, price, list_price)
            a = res.analysis
            lines = [f"✅ <b>Kaydedildi:</b> {escape(res.product.title)}",
                     f"💸 {tl(price)}" + (f" (mağazanın eski fiyatı: {tl(list_price)})" if list_price else ""),
                     f"📈 Bu ürün için {res.observations}. fiyat gözlemi"]
            lines += [f"• {escape(r)}" for r in a.reasons]
            if not a.verified:
                lines.append("ℹ️ İndirimi doğrulayabilmem için aynı linki ileride yeni fiyatla tekrar gönderin "
                             "(en az 7 günlük geçmiş gerekir).")
            self.reply(chat_id, "\n".join(lines))
            if res.deal is not None:
                send_deal_preview(s, self.notifier, res.deal)

    # ================================================================ XML dosyası
    def _on_document(self, chat_id: int, uid: int, doc: dict) -> None:
        name = (doc.get("file_name") or "").lower()
        if not name.endswith((".xml", ".gz", ".zip")):
            self.reply(chat_id, "Yalnızca .xml, .xml.gz veya .zip feed dosyası kabul ediyorum.")
            return
        if (doc.get("file_size") or 0) > MAX_DOWNLOAD_BYTES:
            self.reply(chat_id, "Dosya 20 MB'tan büyük (Telegram bot sınırı). Bu feed için URL kullanalım.")
            return
        configs = load_feed_configs()
        if not configs:
            self.reply(chat_id, "Tanımlı feed eşleştirmesi yok.")
            return
        feeds_dir = self.settings.data_dir / "feeds"
        stamp = utcnow().strftime("%Y%m%d%H%M%S")
        raw = feeds_dir / f"telegram-{stamp}.upload"
        try:
            self.api.download_file(doc["file_id"], raw)
            xml = _decompress(raw, feeds_dir / f"telegram-{stamp}.xml", 500_000_000)
        except ExternalServiceError as exc:
            raw.unlink(missing_ok=True)
            self.reply(chat_id, f"❌ Dosya alınamadı: {escape(str(exc))}")
            return
        if len(configs) == 1:
            self._import_xml(chat_id, next(iter(configs)), xml)
            return
        self._set_conv(uid, {"mode": "pick_feed", "path": str(xml)})
        buttons = [[{"text": n, "callback_data": f"x:{n}"}] for n in configs]
        self.reply(chat_id, "Bu dosya hangi feed'e ait?", {"inline_keyboard": buttons})

    def _import_xml(self, chat_id: int, feed_name: str, path: Path) -> None:
        cfg = load_feed_configs().get(feed_name)
        if cfg is None:
            self.reply(chat_id, "Feed bulunamadı.")
            return
        try:
            with self.sessions() as s:
                report = import_feed_file(s, cfg, path)
                if report.status == "synced":
                    st = report.stats
                    self.reply(chat_id, f"✅ <b>{escape(feed_name)}</b> işlendi: {st['total']} ürün · "
                                        f"{st['new']} yeni · {st['changed']} değişen · "
                                        f"{st['deals_created']} fırsat adayı")
                    sent = preview_new_deals(s, self.notifier, report.deal_ids, self.settings.telegram_max_previews)
                    if len(report.deal_ids) > sent:
                        self.reply(chat_id, f"ℹ️ {len(report.deal_ids) - sent} fırsat daha var: /firsatlar")
                else:
                    self.reply(chat_id, f"❌ Feed reddedildi, veritabanı değişmedi:\n{escape(report.message)}")
        finally:
            path.unlink(missing_ok=True)

    # ================================================================ komutlar
    def _on_command(self, chat_id: int, uid: int, text: str) -> None:
        parts = text.split()
        cmd = parts[0][1:].split("@")[0].lower()
        args = parts[1:]
        handlers = {
            "start": self._cmd_help, "yardim": self._cmd_help, "help": self._cmd_help,
            "durum": self._cmd_status, "maliyet": self._cmd_costs, "butce": self._cmd_budget,
            "durdur": self._cmd_halt, "baslat": self._cmd_resume, "bekleyenler": self._cmd_pending,
            "firsatlar": self._cmd_deals, "feeds": self._cmd_feeds, "demo": self._cmd_demo,
            "iptal": self._cmd_cancel,
        }
        handler = handlers.get(cmd)
        if handler is None:
            self.reply(chat_id, "Bilinmeyen komut. /yardim")
            return
        if cmd != "iptal":
            self._clear_conv(uid)
        handler(chat_id, uid, args)

    def _cmd_help(self, chat_id, uid, args):
        self.reply(chat_id, HELP)

    def _cmd_cancel(self, chat_id, uid, args):
        self._clear_conv(uid)
        self.reply(chat_id, "İptal edildi.")

    def _cmd_status(self, chat_id, uid, args):
        st = self.settings
        with self.sessions() as s:
            halted = is_halted(s)
            pending = s.scalar(select(func.count()).select_from(Deal).where(Deal.status == DealStatus.PENDING_APPROVAL))
            fresh = s.scalar(select(func.count()).select_from(Deal).where(
                Deal.status.in_([DealStatus.NEW, DealStatus.CONTENT_READY])))
            b = budget_status(s, st)
            configs = load_feed_configs()
            stale = stale_feeds(s, configs)
            products = s.scalar(select(func.count()).select_from(Product).where(Product.active.is_(True)))
        integ = st.integration_status()
        lines = [
            "🔴 <b>ACİL DURDURMA AKTİF</b> (/baslat)" if halted else "🟢 <b>Sistem çalışıyor</b>",
            f"Yayın şalteri: {'AÇIK' if st.publishing_enabled else 'kapalı'} · "
            f"DRY_RUN: {'açık' if st.dry_run else 'KAPALI'}",
            f"Onay bekleyen fırsat: <b>{pending}</b> · Onaya sunulmamış yeni fırsat: {fresh}",
            f"Takip edilen aktif ürün: {products}",
            f"Bu ay: {b.spent_usd:.2f}$ / {b.budget_usd:.2f}$ ({_pct(b.ratio)})",
            f"Feed'ler: {sum(c.enabled for c in configs.values())} açık / {len(configs)}"
            + (f" · ⚠️ güncel değil: {', '.join(stale)}" if stale else ""),
            "Entegrasyonlar: " + " · ".join(f"{k} {'✅' if v else '—'}" for k, v in integ.items()),
        ]
        self.reply(chat_id, "\n".join(lines))

    def _cmd_costs(self, chat_id, uid, args):
        with self.sessions() as s:
            sm = summary(s, self.settings)
        by = "\n".join(f"• {k}: {v:.4f}$" for k, v in sm["by_service"].items()) or "• henüz harcama yok"
        self.reply(chat_id, f"💰 <b>Maliyet</b>\nSon 24 saat: {sm['last_24h_usd']:.4f}$\n"
                            f"Bu ay: {sm['month_to_date_usd']:.4f}$ / {sm['budget_usd']:.2f}$\n"
                            f"Ay sonu tahmini: {sm['projected_month_usd']:.2f}$\n{by}")

    def _cmd_budget(self, chat_id, uid, args):
        with self.sessions() as s:
            if not args:
                self.reply(chat_id, f"Aylık bütçe: <b>{get_budget(s, self.settings):.2f}$</b>\n"
                                    "Değiştirmek için: <code>/butce 15</code>")
                return
            value = parse_price(args[0])
            if value is None or value > 10000:
                self.reply(chat_id, "Geçerli bir tutar yazın (0–10000 USD). Örnek: <code>/butce 15</code>")
                return
            old = get_budget(s, self.settings)
            set_budget(s, float(value))
        log.info("Aylık bütçe Telegram'dan değiştirildi: %.2f -> %.2f", old, float(value))
        self.reply(chat_id, f"✅ Aylık bütçe: {old:.2f}$ → <b>{float(value):.2f}$</b>")

    def _cmd_halt(self, chat_id, uid, args):
        with self.sessions() as s:
            set_halted(s, True, uid)
        log.warning("ACİL DURDURMA Telegram'dan açıldı")
        self.reply(chat_id, "🔴 <b>Acil durdurma AKTİF.</b>\nHiçbir yayın ve ücretli AI çağrısı yapılmayacak. "
                            "Fiyat takibi (ücretsiz) devam ediyor.\nKaldırmak için /baslat")

    def _cmd_resume(self, chat_id, uid, args):
        with self.sessions() as s:
            halted = is_halted(s)
        if not halted:
            self.reply(chat_id, "🟢 Sistem zaten çalışıyor.")
            return
        self.reply(chat_id, "Acil durdurmayı kaldırmak istediğinize emin misiniz?",
                   {"inline_keyboard": [[{"text": "🟢 Evet, kaldır", "callback_data": "go:on"},
                                         {"text": "Vazgeç", "callback_data": "noop"}]]})

    def _cmd_pending(self, chat_id, uid, args):
        with self.sessions() as s:
            deals = s.scalars(select(Deal).where(Deal.status == DealStatus.PENDING_APPROVAL)
                              .order_by(Deal.id).limit(10)).all()
            if not deals:
                self.reply(chat_id, "Onay bekleyen fırsat yok.")
                return
            rows = [[{"text": f"#{d.id} {d.product.title[:40]}", "callback_data": f"P:{d.id}"}] for d in deals]
        self.reply(chat_id, "⏳ <b>Onay bekleyenler</b> (tekrar görmek için dokunun):", {"inline_keyboard": rows})

    def _cmd_deals(self, chat_id, uid, args):
        limit = self.settings.telegram_max_previews
        if args and args[0].isdigit():
            limit = max(1, min(int(args[0]), 20))
        with self.sessions() as s:
            sent = preview_new_deals(s, self.notifier, None, limit)
        if not sent:
            self.reply(chat_id, "Onaya sunulacak yeni fırsat yok.")

    def _cmd_feeds(self, chat_id, uid, args):
        configs = load_feed_configs()
        if not configs:
            self.reply(chat_id, "Tanımlı feed yok.")
            return
        lines = ["📦 <b>Feed'ler</b>"]
        with self.sessions() as s:
            for cfg in configs.values():
                stt = s.get(FeedState, cfg.name)
                last = as_utc(stt.last_success_at).strftime("%d.%m %H:%M") if stt and stt.last_success_at else "-"
                lines.append(f"{'🟢' if cfg.enabled else '⚪'} <b>{escape(cfg.name)}</b> · "
                             f"{'URL var' if cfg.resolve_url() else 'URL yok'} · son başarı: {last} UTC · "
                             f"ürün: {stt.last_item_count if stt and stt.last_item_count is not None else '-'}")
                if stt and stt.last_error:
                    lines.append(f"   ⚠️ {escape(stt.last_error[:200])}")
        self.reply(chat_id, "\n".join(lines))

    def _cmd_demo(self, chat_id, uid, args):
        now = utcnow()
        url = f"https://example.com/sepetfy-demo/{now.strftime('%Y%m%d%H%M%S')}"
        title = "[DEMO] Kablosuz Kulaklık X100"
        with self.sessions() as s:
            add_manual_observation(s, url, title, Decimal("1499.90"), now=now - timedelta(days=10),
                                   external_prefix=DEMO_PREFIX)
            res = add_manual_observation(s, url, title, Decimal("1199.90"), Decimal("1999.90"),
                                         now=now, external_prefix=DEMO_PREFIX)
            if res.deal is None:
                self.reply(chat_id, "Demo fırsatı oluşturulamadı.")
                return
            self.reply(chat_id, "🧪 Demo fırsatı gönderiliyor. Butonları deneyebilirsiniz; "
                                "demo içerik hiçbir koşulda yayınlanamaz.")
            send_deal_preview(s, self.notifier, res.deal)

    # ================================================================ butonlar
    def _on_callback(self, cq: dict) -> None:
        user, msg = cq.get("from", {}), cq.get("message") or {}
        chat = msg.get("chat", {})
        if not self._authorized(user, chat):
            self.api.answer_callback(cq["id"], "Yetkiniz yok")
            log.warning("Yetkisiz buton denemesi yok sayıldı")
            return
        uid, chat_id, message_id = user["id"], chat["id"], msg.get("message_id")
        data = cq.get("data") or ""
        kind, _, rest = data.partition(":")

        if kind == "noop":
            self.api.answer_callback(cq["id"])
            return
        if kind == "go" and rest == "on":
            with self.sessions() as s:
                set_halted(s, False, uid)
            log.warning("Acil durdurma Telegram'dan kaldırıldı")
            self.api.answer_callback(cq["id"], "Sistem yeniden çalışıyor")
            self.api.edit_reply_markup(chat_id, message_id, result_keyboard("🟢 Kaldırıldı"))
            return
        if kind == "x":
            conv = self._get_conv(uid)
            self.api.answer_callback(cq["id"])
            if not conv or conv.get("mode") != "pick_feed":
                self.reply(chat_id, "Bu dosyanın süresi doldu, tekrar gönderin.")
                return
            self._clear_conv(uid)
            self._import_xml(chat_id, rest, Path(conv["path"]))
            return

        try:
            deal_id = int(rest.split(":")[0])
        except ValueError:
            self.api.answer_callback(cq["id"], "Geçersiz buton")
            return

        with self.sessions() as s:
            deal = s.get(Deal, deal_id)
            if deal is None:
                self.api.answer_callback(cq["id"], "Fırsat bulunamadı")
                return
            if deal.status != DealStatus.PENDING_APPROVAL:
                self.api.answer_callback(cq["id"], f"Bu fırsat için karar zaten verildi ({deal.status.value})")
                return
            if kind == "P":
                self.api.answer_callback(cq["id"])
                send_deal_preview(s, self.notifier, deal)
            elif kind == "t":
                self._toggle(s, cq, deal, rest.split(":")[1], chat_id, message_id)
            elif kind == "A":
                self._approve(s, cq, deal, uid, chat_id, message_id)
            elif kind == "R":
                self._decide_all(s, deal, ApprovalDecision.REJECT, uid, "Telegram: reddedildi")
                deal.status = DealStatus.REJECTED
                delete_key(s, sel_key(deal.id))
                delete_key(s, later_key(deal.id))
                self.api.answer_callback(cq["id"], "Reddedildi")
                self.api.edit_reply_markup(chat_id, message_id, result_keyboard("❌ Reddedildi"))
            elif kind == "E":
                self.api.answer_callback(cq["id"])
                self._set_conv(uid, {"mode": "edit_note", "deal": deal.id})
                self.reply(chat_id, f"✏️ Fırsat #{deal.id} için ne değişsin? Notunuzu yazın (vazgeçmek için /iptal).")
            elif kind == "L":
                self._decide_all(s, deal, ApprovalDecision.LATER, uid, "Telegram: sonra")
                when = schedule_reminder(s, deal.id)
                self.api.answer_callback(cq["id"], "3 saat sonra hatırlatacağım")
                self.api.edit_reply_markup(chat_id, message_id,
                                           result_keyboard(f"⏰ Hatırlatma: {when.strftime('%H:%M')} UTC"))
            else:
                self.api.answer_callback(cq["id"], "Geçersiz buton")

    def _toggle(self, s, cq, deal: Deal, platform: str, chat_id, message_id) -> None:
        valid = {Platform(c.platform).value for c in pending_contents(deal)}
        if platform not in valid:
            self.api.answer_callback(cq["id"], "Bu platform için içerik yok")
            return
        selected = set(get_json(s, sel_key(deal.id), []) or [])
        selected ^= {platform}
        set_json(s, sel_key(deal.id), sorted(selected))
        self.api.answer_callback(cq["id"])
        self.api.edit_reply_markup(chat_id, message_id, keyboard(deal, sorted(selected)))

    def _decide_all(self, s, deal: Deal, decision: ApprovalDecision, uid: int, note: str) -> None:
        for c in pending_contents(deal):
            record_decision(s, c, decision, uid, self.settings.admin_user_ids, note=note)

    def _approve(self, s, cq, deal: Deal, uid: int, chat_id, message_id) -> None:
        selected = set(get_json(s, sel_key(deal.id), []) or [])
        pending = pending_contents(deal)
        chosen = [c for c in pending if Platform(c.platform).value in selected]
        if not chosen:
            self.api.answer_callback(cq["id"], "En az bir platform seçin")
            return
        try:
            for c in pending:
                if c in chosen:
                    record_decision(s, c, ApprovalDecision.APPROVE, uid, self.settings.admin_user_ids,
                                    [Platform(c.platform).value], note="Telegram: onaylandı")
                else:
                    record_decision(s, c, ApprovalDecision.REJECT, uid, self.settings.admin_user_ids,
                                    note="Telegram: onayda seçilmedi")
        except PublishNotAllowed as exc:
            self.api.answer_callback(cq["id"], str(exc))
            raise
        deal.status = DealStatus.APPROVED
        delete_key(s, sel_key(deal.id))
        delete_key(s, later_key(deal.id))
        names = ", ".join(PLATFORM_LABELS[Platform(c.platform)] for c in chosen)
        self.api.answer_callback(cq["id"], "Onaylandı")
        self.api.edit_reply_markup(chat_id, message_id, result_keyboard(f"✅ Onaylandı: {names}"))
        notes = ["Onay kaydedildi."]
        if deal.product.external_id.startswith(DEMO_PREFIX):
            notes.append("🧪 Bu bir demo; hiçbir koşulda yayınlanmaz.")
        if is_halted(s):
            notes.append("🔴 Acil durdurma aktif: yayın yapılmayacak.")
        if not self.settings.publishing_enabled:
            notes.append("Yayın şalteri kapalı (PUBLISHING_ENABLED=false).")
        notes.append("Platform yayıncıları henüz eklenmedi (FAZ 6); şu an hiçbir şey yayınlanmadı.")
        self.reply(chat_id, f"✅ Fırsat #{deal.id} onaylandı: {names}\n" + "\n".join(notes))

    def _record_edit(self, chat_id: int, uid: int, deal_id: int, note: str) -> None:
        with self.sessions() as s:
            deal = s.get(Deal, deal_id)
            if deal is None or deal.status != DealStatus.PENDING_APPROVAL:
                self.reply(chat_id, "Bu fırsat artık onay beklemiyor.")
                return
            self._decide_all(s, deal, ApprovalDecision.EDIT, uid, note[:1000])
        self.reply(chat_id, f"✏️ Fırsat #{deal_id} için düzenleme notu kaydedildi. Bu içerik artık yayınlanamaz.\n"
                            "Notla otomatik yeniden yazım Claude bağlandığında (FAZ 3) gelecek; yeni sürüm "
                            "tekrar onayınıza sunulacak.")


# ==================================================================== çalıştırıcı
class BotRunner:
    """Telegram'dan uzun sorgulama (long polling) ile güncellemeleri alır."""

    def __init__(self, settings: Settings, api: TelegramAPI | None = None, bot: SepetfyBot | None = None,
                 session_factory: Callable = session_scope):
        if api is None:
            if settings.telegram_bot_token is None:
                raise SepetfyError("TELEGRAM_BOT_TOKEN tanımlı değil")
            api = TelegramAPI(settings.telegram_bot_token.get_secret_value())
        self.api = api
        self.sessions = session_factory
        self.bot = bot or SepetfyBot(settings, api, session_factory=session_factory)

    def _load_offset(self) -> int | None:
        with self.sessions() as s:
            return get_json(s, OFFSET_KEY)

    def _save_offset(self, offset: int) -> None:
        with self.sessions() as s:
            set_json(s, OFFSET_KEY, offset)

    def poll_once(self, offset: int | None, timeout: int = 25) -> int | None:
        for upd in self.api.get_updates(offset, timeout=timeout):
            offset = upd["update_id"] + 1
            self._save_offset(offset)  # önce kaydet: en fazla bir kez işle
            self.bot.handle_update(upd)
        return offset

    def run(self, stop: threading.Event) -> None:
        try:
            me = self.api.get_me()
            self.api.set_commands(COMMANDS)
            log.info("Telegram botu bağlandı: @%s", me.get("username"))
        except TelegramError as exc:
            log.error("Telegram'a bağlanılamadı: %s", exc)
        offset = self._load_offset()
        backoff = 1.0
        while not stop.is_set():
            try:
                offset = self.poll_once(offset)
                backoff = 1.0
            except TelegramError as exc:
                if exc.code == 401:
                    log.error("Telegram token geçersiz (401). .env içindeki TELEGRAM_BOT_TOKEN'ı kontrol edin.")
                    wait = 300.0
                elif exc.code == 409:
                    log.error("Aynı bot başka bir yerde de çalışıyor (409). Yalnızca tek kopya çalışmalı.")
                    wait = 30.0
                else:
                    log.warning("Telegram geçici hatası: %s", exc)
                    wait = float(exc.retry_after or backoff)
                    backoff = min(backoff * 2, 60.0)
                stop.wait(wait)
            except Exception:
                log.exception("Bot döngüsünde beklenmeyen hata")
                stop.wait(5)
        log.info("Telegram botu durdu")
