"""Veri kaynağı kataloğu ve erişim yöntemi değerlendirmesi.

Kural: Resmî API / affiliate feed / RSS / izinli kaynaklar önceliklidir.
Kullanım şartlarını ihlal eden scraping, giriş engeli aşma veya rate limit
atlatma YAPILMAZ. Erişimi doğrulanmamış kaynaklar ``enabled=False`` ile gelir
ve ancak erişim doğrulandıktan sonra açılır.

``access_status`` değerleri:
- ``verified``     : erişim test edildi, çalışıyor
- ``needs_account``: resmî yol var ama hesap/başvuru/onay gerekiyor
- ``unverified``   : resmî ürün verisi yolu henüz doğrulanmadı
- ``manual``       : otomatik erişim yok; manuel giriş / bildirim ile çalışır
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    code: str
    name: str
    base_url: str
    access_method: str  # api | affiliate_feed | affiliate_network | rss | manual
    access_status: str
    min_interval_min: int
    notes: str


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        "trendyol", "Trendyol", "https://www.trendyol.com",
        "affiliate_network", "needs_account", 15,
        "Trendyol'un satıcı (seller) API'si yalnızca kendi ürünleri içindir. "
        "Fırsat yayıncıları için resmî yol Trendyol'un affiliate/influencer programı "
        "veya affiliate ağları üzerinden ürün feed'i; başvuru ve feed erişimi doğrulanmalı.",
    ),
    SourceSpec(
        "hepsiburada", "Hepsiburada", "https://www.hepsiburada.com",
        "affiliate_network", "needs_account", 15,
        "Hepsiburada affiliate programı / affiliate ağı üzerinden ürün feed'i. "
        "Başvuru ve feed formatı doğrulanmalı.",
    ),
    SourceSpec(
        "amazon_tr", "Amazon Türkiye", "https://www.amazon.com.tr",
        "api", "needs_account", 15,
        "Amazon Associates (Ortaklık) Türkiye + ürün API'si. API erişimi genellikle "
        "belirli sayıda nitelikli satıştan sonra açılır; güncel şartlar başvuruda doğrulanmalı.",
    ),
    SourceSpec(
        "n11", "N11", "https://www.n11.com",
        "affiliate_network", "unverified", 30,
        "N11 mağaza API'si satıcılar içindir. Affiliate feed varlığı doğrulanmalı.",
    ),
    SourceSpec(
        "mediamarkt", "MediaMarkt", "https://www.mediamarkt.com.tr",
        "affiliate_network", "unverified", 30,
        "Affiliate ağları (ör. Admitad, Gelir Ortakları vb.) üzerinden feed olup olmadığı doğrulanmalı.",
    ),
    SourceSpec(
        "teknosa", "Teknosa", "https://www.teknosa.com",
        "affiliate_network", "unverified", 30,
        "Affiliate ağı feed'i doğrulanmalı.",
    ),
    SourceSpec(
        "boyner", "Boyner", "https://www.boyner.com.tr",
        "affiliate_network", "unverified", 60,
        "Affiliate ağı feed'i doğrulanmalı.",
    ),
    SourceSpec(
        "zara", "Zara", "https://www.zara.com/tr",
        "manual", "manual", 0,
        "Bilinen kamuya açık affiliate/ürün API'si yok. Otomatik scraping yapılmaz; "
        "manuel ekleme veya resmî kampanya duyuruları.",
    ),
    SourceSpec(
        "brand_campaigns", "Resmî marka kampanya sayfaları", "",
        "rss", "unverified", 60,
        "RSS/bülten sunan markalar tek tek eklenir; robots.txt ve şartlar kontrol edilir.",
    ),
    SourceSpec(
        "manual", "Manuel giriş", "",
        "manual", "verified", 0,
        "Sizin Telegram üzerinden gönderdiğiniz ürün bağlantıları (FAZ 2/5). "
        "Fiyat bilgisi sizin girdiğiniz veya resmî kaynaktan doğrulanan veridir.",
    ),
)


def get_source(code: str) -> SourceSpec | None:
    return next((s for s in SOURCES if s.code == code), None)
