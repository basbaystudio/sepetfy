"""Feed yapılandırması (alan eşleştirme).

Her feed bir JSON dosyasıyla tanımlanır: ``sepetfy/config/feeds/<ad>.json``.
Feed URL'si dosyada YAZMAZ (içinde kişiye özel token olur); ``url_env`` ile adı
verilen ortam değişkeninden / .env'den okunur.

Alan tanımı: XML'deki etiket yolu. Örnekler:
  "price"            -> <item><price>..</price></item>
  "g:price"          -> ad alanı önekleri yok sayılır (Google Shopping)
  "pricing/sale"     -> iç içe etiket
  "@id"              -> <item id="..."> özniteliği
  ["sale_price", "price"] -> sırayla dener, ilk dolu olanı alır
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from sepetfy.app.errors import ConfigError
from sepetfy.config.settings import PROJECT_ROOT

FEEDS_DIR = Path(__file__).resolve().parents[2] / "config" / "feeds"

REQUIRED_FIELDS = ("external_id", "title", "url", "price")
KNOWN_FIELDS = REQUIRED_FIELDS + (
    "brand", "category", "affiliate_url", "image_url", "list_price", "in_stock",
    "stock_qty", "shipping_cost", "coupon_code", "seller_name", "seller_rating",
    "rating", "review_count", "currency",
)


@dataclass(frozen=True)
class FeedConfig:
    name: str
    store_code: str
    item_tag: str
    fields: dict[str, str | list[str]]
    url_env: str | None = None
    enabled: bool = True
    interval_min: int = 60
    decimal: str = "auto"          # auto | dot | comma
    currency: str = "TRY"
    min_item_ratio: float = 0.5    # önceki sürüme göre bu orandan az ürün -> feed reddedilir
    max_invalid_ratio: float = 0.3 # geçersiz ürün oranı bunu aşarsa eşleştirme bozuk sayılır
    missing_runs_to_deactivate: int = 3
    stale_after_hours: int = 24
    max_bytes: int = 500_000_000
    timeout_sec: float = 120
    notes: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        missing = [f for f in REQUIRED_FIELDS if f not in self.fields]
        if missing:
            raise ConfigError(f"Feed '{self.name}': zorunlu alan eşleştirmesi eksik: {missing}")
        unknown = [f for f in self.fields if f not in KNOWN_FIELDS]
        if unknown:
            raise ConfigError(f"Feed '{self.name}': bilinmeyen alan(lar): {unknown}")
        if self.decimal not in ("auto", "dot", "comma"):
            raise ConfigError(f"Feed '{self.name}': decimal auto|dot|comma olmalı")

    def resolve_url(self) -> str | None:
        if not self.url_env:
            return None
        val = os.environ.get(self.url_env)
        if not val:
            val = dotenv_values(PROJECT_ROOT / ".env").get(self.url_env)
        return val.strip() if val and val.strip() else None

    @classmethod
    def from_dict(cls, data: dict) -> FeedConfig:
        known = set(cls.__dataclass_fields__) - {"extra"}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["extra"] = {k: v for k, v in data.items() if k not in known and not k.startswith("_")}
        return cls(**kwargs)


def load_feed_configs(directory: Path = FEEDS_DIR) -> dict[str, FeedConfig]:
    configs: dict[str, FeedConfig] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            cfg = FeedConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{path.name} okunamadı: {exc}") from exc
        if cfg.name in configs:
            raise ConfigError(f"Aynı feed adı iki kez tanımlı: {cfg.name}")
        configs[cfg.name] = cfg
    return configs
