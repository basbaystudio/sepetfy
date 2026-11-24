"""Telegram Bot API için ince, senkron istemci (resmî HTTP API: https://core.telegram.org/bots/api).

GÜVENLİK: Bot token'ı istek adresinin (URL) içindedir. httpx hata mesajları URL'yi
içerebileceğinden hiçbir hata mesajına URL veya token aktarılmaz.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from sepetfy.app.errors import ExternalServiceError

BASE_URL = "https://api.telegram.org"
SERVICE = "telegram"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API ile indirilebilecek en büyük dosya
MAX_TEXT = 4096
MAX_CAPTION = 1024


class TelegramError(ExternalServiceError):
    def __init__(self, message: str, *, code: int | None = None, retryable: bool = False,
                 retry_after: int | None = None):
        super().__init__(SERVICE, message, retryable=retryable)
        self.code = code
        self.retry_after = retry_after


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TelegramAPI:
    def __init__(self, token: str, client: httpx.Client | None = None, timeout: float = 40):
        self._token = token
        self._client = client or httpx.Client()
        self._timeout = timeout

    def call(self, method: str, payload: dict | None = None, *, timeout: float | None = None) -> Any:
        url = f"{BASE_URL}/bot{self._token}/{method}"
        try:
            resp = self._client.post(url, json=payload or {}, timeout=timeout or self._timeout)
        except httpx.HTTPError as exc:
            raise TelegramError(f"{method}: {type(exc).__name__}", retryable=True) from None
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {resp.status_code}", code=resp.status_code,
                                retryable=resp.status_code >= 500) from None
        if not data.get("ok"):
            code = data.get("error_code", resp.status_code)
            desc = str(data.get("description", ""))[:200].replace(self._token, "***")
            retry_after = (data.get("parameters") or {}).get("retry_after")
            raise TelegramError(f"{method}: {code} {desc}", code=code,
                                retryable=code == 429 or code >= 500, retry_after=retry_after)
        return data["result"]

    # ---------------------------------------------------------------- yöntemler
    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=timeout + 15)

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": truncate(text, MAX_TEXT), "parse_mode": "HTML",
                   "link_preview_options": {"is_disabled": True}}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", payload)

    def send_photo(self, chat_id: int, photo_url: str, caption: str, reply_markup: dict | None = None) -> dict:
        payload = {"chat_id": chat_id, "photo": photo_url, "caption": truncate(caption, MAX_CAPTION),
                   "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call("sendPhoto", payload)

    def edit_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict | None) -> Any:
        return self.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                    "reply_markup": reply_markup or {"inline_keyboard": []}})

    def answer_callback(self, callback_id: str, text: str | None = None) -> Any:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = truncate(text, 200)
        return self.call("answerCallbackQuery", payload)

    def set_commands(self, commands: list[tuple[str, str]]) -> Any:
        return self.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})

    def download_file(self, file_id: str, dest: Path, max_bytes: int = MAX_DOWNLOAD_BYTES) -> Path:
        info = self.call("getFile", {"file_id": file_id})
        if info.get("file_size") and info["file_size"] > max_bytes:
            raise TelegramError("dosya çok büyük (Bot API sınırı 20 MB)")
        url = f"{BASE_URL}/file/bot{self._token}/{info['file_path']}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with self._client.stream("GET", url, timeout=120) as resp, open(dest, "wb") as out:
                if resp.status_code != 200:
                    raise TelegramError(f"getFile indirme: HTTP {resp.status_code}")
                for chunk in resp.iter_bytes(1 << 16):
                    size += len(chunk)
                    if size > max_bytes:
                        raise TelegramError("dosya çok büyük (Bot API sınırı 20 MB)")
                    out.write(chunk)
        except httpx.HTTPError as exc:
            dest.unlink(missing_ok=True)
            raise TelegramError(f"dosya indirme: {type(exc).__name__}", retryable=True) from None
        except TelegramError:
            dest.unlink(missing_ok=True)
            raise
        return dest

    def close(self) -> None:
        self._client.close()
