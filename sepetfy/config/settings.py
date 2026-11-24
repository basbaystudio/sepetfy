"""Uygulama ayarları.

Tüm ayarlar ortam değişkenlerinden / .env dosyasından okunur. Gizli değerler
``SecretStr`` olarak tutulur; böylece yanlışlıkla loglanırlarsa ``**********``
olarak görünürler.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Genel
    app_env: str = "development"
    log_level: str = "INFO"
    timezone: str = "Europe/Istanbul"
    log_dir: Path = PROJECT_ROOT / "logs"

    # Veritabanı
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'sepetfy.db'}"

    # Güvenlik şalterleri (varsayılan: kapalı / deneme modu)
    publishing_enabled: bool = False
    dry_run: bool = True

    # Telegram
    telegram_bot_token: SecretStr | None = None
    telegram_admin_user_ids: str = ""

    # AI servisleri
    anthropic_api_key: SecretStr | None = None
    claude_model: str = "claude-opus-5-5"
    gemini_api_key: SecretStr | None = None
    kling_access_key: SecretStr | None = None
    kling_secret_key: SecretStr | None = None

    # Maliyet
    monthly_budget_usd: float = Field(default=30.0, ge=0)
    budget_warn_ratio: float = Field(default=0.8, gt=0, le=1)
    budget_hard_stop: bool = True

    # Zamanlama
    default_check_interval_min: int = Field(default=15, ge=1)

    # Yedekleme
    backup_dir: Path = PROJECT_ROOT / "backups"
    backup_keep: int = Field(default=14, ge=1)

    @field_validator(
        "telegram_bot_token",
        "anthropic_api_key",
        "gemini_api_key",
        "kling_access_key",
        "kling_secret_key",
        mode="before",
    )
    @classmethod
    def _empty_secret_is_none(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_path(cls, v: str) -> str:
        # "sqlite:///data/x.db" gibi göreli yolları proje köküne göre çöz.
        prefix = "sqlite:///"
        if v.startswith(prefix) and not v.startswith(prefix + "/") and ":memory:" not in v:
            return prefix + str(PROJECT_ROOT / v[len(prefix):])
        return v

    @field_validator("log_dir", "backup_dir")
    @classmethod
    def _resolve_dir(cls, v: Path) -> Path:
        return v if v.is_absolute() else PROJECT_ROOT / v

    @property
    def admin_user_ids(self) -> frozenset[int]:
        ids: set[int] = set()
        for part in self.telegram_admin_user_ids.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return frozenset(ids)

    def secret_values(self) -> list[str]:
        """Loglardan maskelenecek gizli değerler."""
        out = []
        for s in (
            self.telegram_bot_token,
            self.anthropic_api_key,
            self.gemini_api_key,
            self.kling_access_key,
            self.kling_secret_key,
        ):
            if s is not None:
                val = s.get_secret_value()
                if len(val) >= 6:
                    out.append(val)
        return out

    def integration_status(self) -> dict[str, bool]:
        return {
            "telegram": self.telegram_bot_token is not None and bool(self.admin_user_ids),
            "claude": self.anthropic_api_key is not None,
            "gemini": self.gemini_api_key is not None,
            "kling": self.kling_access_key is not None and self.kling_secret_key is not None,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
