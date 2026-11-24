"""Yöneticilere (yalnızca TELEGRAM_ADMIN_USER_IDS) Telegram bildirimi.

- ``send``: düz mesaj / fotoğraflı mesaj (+ butonlar).
- ``alert``: aynı anahtarlı uyarıyı bekleme süresi dolmadan tekrar göndermez (uyarı yağmurunu önler).
Telegram yapılandırılmamışsa veya ulaşılamıyorsa uyarılar yine loga yazılır; sistem durmaz.
"""

from __future__ import annotations

import logging
import threading
import time

from sepetfy.app.errors import ExternalServiceError
from sepetfy.config import Settings
from sepetfy.integrations.telegram_api import TelegramAPI, TelegramError

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings, api: TelegramAPI | None = None,
                 clock=time.monotonic):
        self.settings = settings
        self.api = api
        if self.api is None and settings.telegram_bot_token is not None:
            self.api = TelegramAPI(settings.telegram_bot_token.get_secret_value())
        self._last_alert: dict[str, float] = {}
        self._lock = threading.Lock()
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self.api is not None and bool(self.settings.admin_user_ids)

    def send(self, text: str, *, reply_markup: dict | None = None, photo: str | None = None) -> list[tuple[int, int]]:
        """Tüm yöneticilere gönderir; (chat_id, message_id) listesi döner."""
        sent: list[tuple[int, int]] = []
        if not self.enabled:
            log.info("Telegram yapılandırılmadı, bildirim yalnızca loga yazıldı")
            return sent
        for chat_id in sorted(self.settings.admin_user_ids):
            try:
                msg = None
                if photo:
                    try:
                        msg = self.api.send_photo(chat_id, photo, text, reply_markup)
                    except TelegramError as exc:
                        # Görsel adresi geçersizse metin olarak gönder
                        log.warning("Fotoğraf gönderilemedi, metne geçiliyor: %s", exc)
                if msg is None:
                    msg = self.api.send_message(chat_id, text, reply_markup)
                sent.append((chat_id, msg["message_id"]))
            except ExternalServiceError as exc:
                log.error("Telegram bildirimi gönderilemedi (%s): %s", chat_id, exc)
        return sent

    def alert(self, key: str, text: str, cooldown_hours: float = 6) -> bool:
        log.warning("UYARI [%s]: %s", key, text)
        now = self._clock()
        with self._lock:
            last = self._last_alert.get(key)
            if last is not None and now - last < cooldown_hours * 3600:
                return False
            self._last_alert[key] = now
        return bool(self.send(f"⚠️ <b>Uyarı</b>\n{text}"))


_notifier: Notifier | None = None


def configure_notifier(notifier: Notifier | None) -> None:
    global _notifier
    _notifier = notifier


def get_notifier() -> Notifier | None:
    return _notifier


def alert(key: str, text: str, cooldown_hours: float = 6) -> None:
    """Modüllerin Telegram'a bağımlı olmadan uyarı göndermesi için."""
    if _notifier is None:
        log.warning("UYARI [%s]: %s", key, text)
        return
    try:
        _notifier.alert(key, text, cooldown_hours)
    except Exception:  # bildirim hatası asıl işi asla durdurmamalı
        log.exception("Uyarı gönderilemedi: %s", key)
