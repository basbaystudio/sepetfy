"""Telegram yönetim botu: yetki, onay akışı, komutlar, manuel ürün, XML yükleme, acil durdurma."""

from __future__ import annotations

import shutil
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from sepetfy.analytics.costs import ensure_budget, get_budget
from sepetfy.app.errors import PublishNotAllowed, SystemHalted
from sepetfy.app.runtime import is_halted
from sepetfy.approval.bot import BotRunner, SepetfyBot
from sepetfy.approval.guard import authorize_publication
from sepetfy.approval.preview import due_reminders, preview_new_deals
from sepetfy.collectors.manual import add_manual_observation
from sepetfy.content.telegram_post import build_telegram_post, tl
from sepetfy.database import session_scope
from sepetfy.database.models import (
    ApprovalDecision, ApprovalRecord, ContentItem, ContentStatus, Deal, DealStatus, Platform, PriceSnapshot,
    Product, utcnow,
)
from sepetfy.database.seed import seed_stores
from sepetfy.integrations.telegram_api import TelegramAPI, TelegramError
from sepetfy.notifications.telegram import Notifier
from tests.conftest import ADMIN_ID

FIXTURES = Path(__file__).parent / "fixtures"
STRANGER = 999_000_111


class FakeAPI:
    """Telegram API yerine geçer; gönderilen her şeyi kaydeder."""

    def __init__(self):
        self.messages: list[dict] = []
        self.markups: list[dict] = []
        self.answers: list[tuple[str, str | None]] = []
        self.files: dict[str, Path] = {}
        self.updates: list[dict] = []
        self._mid = 1000

    def _next(self) -> dict:
        self._mid += 1
        return {"message_id": self._mid}

    def send_message(self, chat_id, text, reply_markup=None):
        r = self._next()
        self.messages.append({"chat_id": chat_id, "text": text, "markup": reply_markup, "id": r["message_id"]})
        return r

    def send_photo(self, chat_id, photo, caption, reply_markup=None):
        if photo == "https://bad.example/img.jpg":
            raise TelegramError("sendPhoto: 400 wrong file", code=400)
        r = self._next()
        self.messages.append({"chat_id": chat_id, "text": caption, "markup": reply_markup, "id": r["message_id"],
                              "photo": photo})
        return r

    def edit_reply_markup(self, chat_id, message_id, reply_markup):
        self.markups.append({"message_id": message_id, "markup": reply_markup})

    def answer_callback(self, callback_id, text=None):
        self.answers.append((callback_id, text))

    def download_file(self, file_id, dest, max_bytes=0):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.files[file_id], dest)
        return dest

    def get_updates(self, offset, timeout=25):
        out, self.updates = self.updates, []
        return out

    def get_me(self):
        return {"username": "sepetfy_test_bot"}

    def set_commands(self, commands):
        pass

    # yardımcılar
    def texts(self) -> list[str]:
        return [m["text"] for m in self.messages]

    def last_text(self) -> str:
        return self.messages[-1]["text"]

    def card(self) -> dict:
        return [m for m in self.messages if m["markup"] and "ONAYLA" in str(m["markup"])][-1]


@pytest.fixture
def api():
    return FakeAPI()


@pytest.fixture
def bot(settings, api):
    with session_scope() as s:
        seed_stores(s)
    return SepetfyBot(settings, api, notifier=Notifier(settings, api))


_uid = [0]


def message(text=None, uid=ADMIN_ID, chat_type="private", document=None) -> dict:
    _uid[0] += 1
    msg = {"message_id": _uid[0], "from": {"id": uid}, "chat": {"id": uid, "type": chat_type}}
    if text is not None:
        msg["text"] = text
    if document is not None:
        msg["document"] = document
    return {"update_id": _uid[0], "message": msg}


def callback(data, message_id, uid=ADMIN_ID, chat_type="private") -> dict:
    _uid[0] += 1
    return {"update_id": _uid[0], "callback_query": {
        "id": f"cb{_uid[0]}", "from": {"id": uid}, "data": data,
        "message": {"message_id": message_id, "chat": {"id": uid, "type": chat_type}}}}


def demo_deal(bot, api) -> tuple[int, int]:
    bot.handle_update(message("/demo"))
    card = api.card()
    deal_id = int(card["markup"]["inline_keyboard"][1][0]["callback_data"].split(":")[1])
    return deal_id, card["id"]


def decisions(deal_id) -> list[ApprovalRecord]:
    with session_scope() as s:
        return s.scalars(select(ApprovalRecord).join(ContentItem).where(ContentItem.deal_id == deal_id)
                         .order_by(ApprovalRecord.id)).all()


# ---------------------------------------------------------------- yetki
def test_stranger_is_ignored(bot, api):
    bot.handle_update(message("/durum", uid=STRANGER))
    bot.handle_update(message("/durdur", uid=STRANGER))
    assert api.messages == []
    with session_scope() as s:
        assert not is_halted(s)


def test_admin_in_group_chat_is_ignored(bot, api):
    bot.handle_update(message("/durdur", chat_type="group"))
    assert api.messages == []


def test_setup_mode_tells_user_their_id(settings, api):
    settings.telegram_admin_user_ids = ""
    b = SepetfyBot(settings, api, notifier=Notifier(settings, api))
    b.handle_update(message("/start", uid=424242))
    assert "424242" in api.last_text() and "TELEGRAM_ADMIN_USER_IDS" in api.last_text()
    b.handle_update(message("/durdur", uid=424242))  # yine de hiçbir komut çalışmaz
    with session_scope() as s:
        assert not is_halted(s)


def test_stranger_cannot_press_buttons(bot, api):
    deal_id, mid = demo_deal(bot, api)
    bot.handle_update(callback(f"A:{deal_id}", mid, uid=STRANGER))
    assert api.answers[-1][1] == "Yetkiniz yok"
    assert decisions(deal_id) == []


# ---------------------------------------------------------------- önizleme ve onay
def test_demo_preview_card_and_content(bot, api):
    deal_id, _ = demo_deal(bot, api)
    card = api.card()
    assert "DEMO" in card["text"] and "Doğrulanmış indirim" in card["text"] and "%20" in card["text"]
    assert "Şüpheli eski fiyat" in card["text"]  # demo, mağazanın şişirilmiş eski fiyatını içerir
    buttons = str(card["markup"])
    for label in ("ONAYLA", "REDDET", "DÜZENLE", "SONRA", "☑ Telegram"):
        assert label in buttons
    assert any("Telegram içeriği" in t for t in api.texts())
    with session_scope() as s:
        assert s.get(Deal, deal_id).status == DealStatus.PENDING_APPROVAL


def test_approve_records_decision_and_blocks_second_press(bot, api):
    deal_id, mid = demo_deal(bot, api)
    bot.handle_update(callback(f"A:{deal_id}", mid))
    recs = decisions(deal_id)
    assert [(r.decision, r.platforms, r.actor_telegram_id) for r in recs] == [
        (ApprovalDecision.APPROVE, ["telegram"], ADMIN_ID)]
    assert "Onaylandı" in str(api.markups[-1]["markup"])
    assert "hiçbir şey yayınlanmadı" in api.last_text()
    bot.handle_update(callback(f"A:{deal_id}", mid))
    assert "zaten" in api.answers[-1][1]
    assert len(decisions(deal_id)) == 1


def test_demo_content_can_never_be_published(bot, api):
    deal_id, mid = demo_deal(bot, api)
    bot.handle_update(callback(f"A:{deal_id}", mid))
    with session_scope() as s:
        content = s.scalar(select(ContentItem).where(ContentItem.deal_id == deal_id))
        with pytest.raises(PublishNotAllowed, match="Demo"):
            authorize_publication(s, content.id, Platform.TELEGRAM, frozenset({ADMIN_ID}))


def test_unselected_platform_cannot_be_approved(bot, api):
    deal_id, mid = demo_deal(bot, api)
    bot.handle_update(callback(f"t:{deal_id}:telegram", mid))
    assert "☐ Telegram" in str(api.markups[-1]["markup"])
    bot.handle_update(callback(f"A:{deal_id}", mid))
    assert api.answers[-1][1] == "En az bir platform seçin"
    assert decisions(deal_id) == []


def test_reject(bot, api):
    deal_id, mid = demo_deal(bot, api)
    bot.handle_update(callback(f"R:{deal_id}", mid))
    assert [r.decision for r in decisions(deal_id)] == [ApprovalDecision.REJECT]
    with session_scope() as s:
        assert s.get(Deal, deal_id).status == DealStatus.REJECTED


def test_edit_asks_for_note_and_records_it(bot, api):
    deal_id, mid = demo_deal(bot, api)
    bot.handle_update(callback(f"E:{deal_id}", mid))
    assert "ne değişsin" in api.last_text()
    bot.handle_update(message("Fiyatı başa al, daha kısa yaz"))
    (rec,) = decisions(deal_id)
    assert rec.decision == ApprovalDecision.EDIT and rec.note == "Fiyatı başa al, daha kısa yaz"
    with session_scope() as s:
        c = s.scalar(select(ContentItem).where(ContentItem.deal_id == deal_id))
        assert c.status == ContentStatus.NEEDS_EDIT


def test_later_schedules_reminder(bot, api):
    deal_id, mid = demo_deal(bot, api)
    bot.handle_update(callback(f"L:{deal_id}", mid))
    assert decisions(deal_id)[0].decision == ApprovalDecision.LATER
    with session_scope() as s:
        assert due_reminders(s) == []
        assert due_reminders(s, utcnow() + timedelta(hours=3, minutes=1)) == [deal_id]
        assert s.get(Deal, deal_id).status == DealStatus.PENDING_APPROVAL


def test_pending_list_and_resend(bot, api):
    deal_id, _ = demo_deal(bot, api)
    bot.handle_update(message("/bekleyenler"))
    assert f"P:{deal_id}" in str(api.messages[-1]["markup"])
    before = len(api.messages)
    bot.handle_update(callback(f"P:{deal_id}", 1))
    assert len(api.messages) > before and "ONAYLA" in str(api.card()["markup"])


# ---------------------------------------------------------------- acil durdurma ve bütçe
def test_halt_blocks_paid_calls_and_publishing_then_resume(bot, api, settings):
    from tests.test_approval_guard import FakeTelegramPublisher

    bot.handle_update(message("/durdur"))
    assert "AKTİF" in api.last_text()
    with session_scope() as s:
        assert is_halted(s)
        with pytest.raises(SystemHalted):
            ensure_budget(s, settings, 0.01)
        with pytest.raises(PublishNotAllowed, match="Acil durdurma"):
            FakeTelegramPublisher(settings).publish(s, 1)
    bot.handle_update(message("/baslat"))
    assert "emin misiniz" in api.last_text()
    bot.handle_update(callback("go:on", api.messages[-1]["id"]))
    with session_scope() as s:
        assert not is_halted(s)


def test_budget_command(bot, api, settings):
    bot.handle_update(message("/butce"))
    assert "10.00$" in api.last_text()
    bot.handle_update(message("/butce 15,5"))
    assert "15.50$" in api.last_text()
    bot.handle_update(message("/butce çok"))
    assert "Geçerli bir tutar" in api.last_text()
    with session_scope() as s:
        assert get_budget(s, settings) == 15.5


def test_status_and_costs_commands(bot, api):
    bot.handle_update(message("/durum"))
    assert "Sistem çalışıyor" in api.last_text() and "Onay bekleyen" in api.last_text()
    bot.handle_update(message("/maliyet"))
    assert "henüz harcama yok" in api.last_text()
    bot.handle_update(message("/feeds"))
    assert "hepsiburada" in api.last_text()
    bot.handle_update(message("/bilinmeyen"))
    assert "Bilinmeyen komut" in api.last_text()


# ---------------------------------------------------------------- manuel ürün
def test_manual_product_conversation(bot, api):
    bot.handle_update(message("https://www.hepsiburada.com/kulaklik-p-HBC123?utm_source=x"))
    assert "hepsiburada" in api.last_text()
    bot.handle_update(message("Kablosuz Kulaklık"))
    bot.handle_update(message("bilmem"))
    assert "anlayamadım" in api.last_text().lower()
    bot.handle_update(message("1.299,90"))
    bot.handle_update(message("yok"))
    assert "Kaydedildi" in api.last_text() and "1.299,90 TL" in api.last_text()
    assert "İlk gözlem" in api.last_text()
    # aynı ürün (izleme parametresi farklı) tekrar gönderilince ad sorulmaz
    bot.handle_update(message("https://www.hepsiburada.com/kulaklik-p-HBC123?utm_source=y"))
    assert "zaten takip ediyoruz" in api.last_text()
    bot.handle_update(message("1.199,90"))
    bot.handle_update(message("1.499,90"))
    assert "2. fiyat gözlemi" in api.last_text()
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Product)) == 1
        assert s.scalar(select(func.count()).select_from(PriceSnapshot)) == 2


def test_manual_one_shot_message(bot, api):
    bot.handle_update(message("https://www.trendyol.com/x/urun-p-1\nTermos 500 ml\n349,90\n499,90"))
    assert "Kaydedildi" in api.last_text() and "499,90 TL" in api.last_text()


def test_invalid_link_rejected(bot, api):
    bot.handle_update(message("http://localhost/abc"))
    assert "Geçerli" in api.last_text()


def test_cancel_conversation(bot, api):
    bot.handle_update(message("https://www.n11.com/urun/abc"))
    bot.handle_update(message("/iptal"))
    bot.handle_update(message("Bu artık ürün adı değil"))
    assert "linkini gönderin" in api.last_text()


def test_manual_history_creates_verified_deal(bot, settings):
    url = "https://www.amazon.com.tr/dp/B0TEST"
    now = utcnow()
    with session_scope() as s:
        add_manual_observation(s, url, "Kahve Makinesi", Decimal("3000"), now=now - timedelta(days=8))
        res = add_manual_observation(s, url, "Kahve Makinesi", Decimal("2400"), now=now)
        assert res.deal is not None and res.deal.verified_discount_pct == 20.0
        assert res.product.store.code == "amazon_tr"


# ---------------------------------------------------------------- XML dosyası
def test_xml_upload_imports_feed(bot, api):
    api.files["F1"] = FIXTURES / "google_shopping.xml"
    bot.handle_update(message(document={"file_id": "F1", "file_name": "feed.xml", "file_size": 2000}))
    assert "işlendi" in api.last_text() and "2 ürün" in api.last_text()


def test_non_xml_upload_rejected(bot, api):
    bot.handle_update(message(document={"file_id": "F2", "file_name": "foto.jpg", "file_size": 10}))
    assert "Yalnızca" in api.last_text()


def test_oversized_upload_rejected(bot, api):
    bot.handle_update(message(document={"file_id": "F3", "file_name": "b.xml", "file_size": 50_000_000}))
    assert "20 MB" in api.last_text()


# ---------------------------------------------------------------- toplu önizleme, şablon
def _make_deal(title, old, new, days=8):
    url = f"https://www.teknosa.com/{title.replace(' ', '-')}"
    now = utcnow()
    with session_scope() as s:
        add_manual_observation(s, url, title, Decimal(old), now=now - timedelta(days=days))
        return add_manual_observation(s, url, title, Decimal(new), now=now).deal.id


def test_preview_new_deals_respects_limit_and_order(bot, api):
    small = _make_deal("Ürün A", "100", "85")   # %15
    big = _make_deal("Ürün B", "100", "60")     # %40
    with session_scope() as s:
        assert preview_new_deals(s, bot.notifier, None, limit=1) == 1
        assert s.get(Deal, big).status == DealStatus.PENDING_APPROVAL
        assert s.get(Deal, small).status == DealStatus.NEW


def test_bad_image_falls_back_to_text(bot, api):
    deal_id = _make_deal("Görselli Ürün", "500", "300")
    with session_scope() as s:
        s.get(Deal, deal_id).product.image_url = "https://bad.example/img.jpg"
    with session_scope() as s:
        preview_new_deals(s, bot.notifier, [deal_id])
    assert "photo" not in api.card()


def test_telegram_post_template_is_honest_and_safe():
    from sepetfy.database.models import Deal as D, PriceSnapshot as P, Product as Pr

    product = Pr(title="<script>x</script> Çanta", url="https://e.com/p", affiliate_url="https://aff.e.com/1")
    snap = P(price=Decimal("899.9"), shipping_cost=Decimal("0"), coupon_code="SEPET10", coupon_verified=False)
    deal = D(discount_verified=True, reference_price=Decimal("1299"), verified_discount_pct=30.7)
    post = build_telegram_post(product, snap, deal)
    assert "&lt;script&gt;" in post and "<script>" not in post
    assert "<s>1.299,00 TL</s>" in post and "<b>899,90 TL</b>" in post and "%30.7" in post
    assert "kupon doğrulanmadı" in post and "Ücretsiz" in post
    assert "https://aff.e.com/1" in post and "komisyon" in post
    unverified = build_telegram_post(product, snap, D(discount_verified=False))
    assert "indirim" not in unverified.split("\n")[1]
    assert tl(Decimal("1234567.5")) == "1.234.567,50 TL"


# ---------------------------------------------------------------- çalıştırıcı ve bildirim
def test_runner_saves_offset_before_handling_and_survives_errors(settings, api):
    with session_scope() as s:
        seed_stores(s)

    class Boom(SepetfyBot):
        def _on_command(self, *a):
            raise RuntimeError("patladı")

    runner = BotRunner(settings, api=api, bot=Boom(settings, api, notifier=Notifier(settings, api)))
    api.updates = [message("/durum"), message("/durum")]
    last_id = api.updates[-1]["update_id"]
    offset = runner.poll_once(None)
    assert offset == last_id + 1                 # Telegram'a "bunları aldım" denir
    assert runner._load_offset() == last_id + 1  # yeniden başlatmada aynı güncellemeler işlenmez
    assert sum("hata oluştu" in t for t in api.texts()) == 2  # hata döngüyü durdurmadı


def test_alert_cooldown(settings, api):
    t = [0.0]
    n = Notifier(settings, api, clock=lambda: t[0])
    assert n.alert("k", "bütçe") is True
    assert n.alert("k", "bütçe") is False
    t[0] = 6 * 3600 + 1
    assert n.alert("k", "bütçe") is True
    assert len(api.messages) == 2


# ---------------------------------------------------------------- gerçek HTTP istemcisi
def test_telegram_api_does_not_leak_token():
    token = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        if req.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": False, "error_code": 401, "description": f"Unauthorized {token}"})
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": False, "error_code": 429, "description": "Too Many",
                                             "parameters": {"retry_after": 7}})
        raise httpx.ConnectError(f"boom {req.url}")

    api = TelegramAPI(token, httpx.Client(transport=httpx.MockTransport(handler)))
    assert api.send_message(1, "x" * 5000)["message_id"] == 7
    with pytest.raises(TelegramError) as e1:
        api.get_me()
    assert e1.value.code == 401 and not e1.value.retryable and token not in str(e1.value)
    with pytest.raises(TelegramError) as e2:
        api.get_updates(None)
    assert e2.value.retryable and e2.value.retry_after == 7
    with pytest.raises(TelegramError) as e3:
        api.answer_callback("1")
    assert token not in str(e3.value) and "ConnectError" in str(e3.value)
    assert seen[0].endswith("/sendMessage")


# ---------------------------------------------------------------- uçtan uca: gerçek HTTP istemcisi + döngü
def test_full_polling_loop_with_fake_telegram_server(settings):
    """Gerçek TelegramAPI + BotRunner.run, Telegram'ı taklit eden sunucuya karşı: /demo -> ONAYLA."""
    import json
    import threading

    with session_scope() as s:
        seed_stores(s)
    stop = threading.Event()
    sent: list[dict] = []
    state = {"step": 0}

    def handler(req: httpx.Request):
        method = req.url.path.rsplit("/", 1)[1]
        body = json.loads(req.content or b"{}")
        if method in ("getMe", "setMyCommands", "answerCallbackQuery", "editMessageReplyMarkup"):
            sent.append({"method": method, **body})
            return httpx.Response(200, json={"ok": True, "result": True if method != "getMe" else {"username": "b"}})
        if method in ("sendMessage", "sendPhoto"):
            sent.append({"method": method, **body})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})
        assert method == "getUpdates"
        state["step"] += 1
        chat = {"id": ADMIN_ID, "type": "private"}
        if state["step"] == 1:
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 500, "message": {"message_id": 1, "from": {"id": ADMIN_ID}, "chat": chat,
                                               "text": "/demo"}}]})
        if state["step"] == 2:
            assert body["offset"] == 501
            card = next(m for m in sent if "reply_markup" in m and "ONAYLA" in json.dumps(m["reply_markup"],
                                                                                           ensure_ascii=False))
            approve = card["reply_markup"]["inline_keyboard"][1][0]["callback_data"]
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 501, "callback_query": {"id": "q1", "from": {"id": ADMIN_ID}, "data": approve,
                                                      "message": {"message_id": 3, "chat": chat}}}]})
        stop.set()
        return httpx.Response(200, json={"ok": True, "result": []})

    api = TelegramAPI("1:" + "A" * 35, httpx.Client(transport=httpx.MockTransport(handler)))
    BotRunner(settings, api=api).run(stop)

    methods = [m["method"] for m in sent]
    assert methods[:2] == ["getMe", "setMyCommands"]
    assert "editMessageReplyMarkup" in methods and "answerCallbackQuery" in methods
    for m in sent:
        if m["method"] in ("sendMessage", "sendPhoto"):
            assert m["parse_mode"] == "HTML" and m["chat_id"] == ADMIN_ID
            assert len(m.get("text") or m.get("caption")) <= 4096
        for row in (m.get("reply_markup") or {}).get("inline_keyboard", []):
            for btn in row:
                assert len(btn["callback_data"].encode()) <= 64  # Telegram sınırı
    assert any("onaylandı" in (m.get("text") or "") for m in sent)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(ApprovalRecord)) == 1


def test_callback_data_fits_telegram_limit_for_large_ids(settings):
    from sepetfy.approval.preview import keyboard

    deal = Deal(id=10**12)
    deal.contents = [ContentItem(platform=p, status=ContentStatus.PENDING_APPROVAL) for p in Platform]
    for row in keyboard(deal, [p.value for p in Platform])["inline_keyboard"]:
        for btn in row:
            assert len(btn["callback_data"].encode()) <= 64
