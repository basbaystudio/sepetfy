# Sepetfy Mimarisi

## Genel akış

```
 ┌───────────── Kaynaklar (yalnızca izinli yollar) ─────────────┐
 │ Affiliate feed/API · Amazon ürün API · RSS · Manuel (Telegram)│
 └──────────────────────────────┬───────────────────────────────┘
                                ▼
  collectors/ ──► price_tracker/ ──► deal_scoring/ ──► ai/ (Claude)
  (toplama)       (fiyat geçmişi,     (kural tabanlı ön     (yalnızca aday
                   sahte indirim)      puan, A/B/C/RED)       ürünler, önbellekli)
                                                               │
                         media/ (Gemini görsel, Kling video) ◄─┤ yalnızca A/B
                         content/ (platform şablonları)     ◄──┘
                                │
                                ▼
                 approval/ ── Telegram önizleme ── SİZ: ONAYLA / REDDET / DÜZENLE / SONRA
                                │  (append-only onay kaydı + içerik özeti)
                                ▼
                 publishers/BasePublisher.publish()  ◄── TEK yayın kapısı
                   1) PUBLISHING_ENABLED şalteri
                   2) authorize_publication (onay kapısı)
                   3) UNIQUE(content_id, platform) yayın kilidi
                   4) DRY_RUN kontrolü
                                ▼
           Telegram · Instagram · TikTok · Pinterest · X · (WhatsApp: manuel paket)
                                ▼
                 analytics/ (performans, maliyet, haftalık rapor)
```

## Tasarım ilkeleri

1. **Onaysız yayın imkânsız olmalı** — tek bir kurala değil, katmanlara dayanır:
   - Uygulama: `BasePublisher.publish` `@final`; alt sınıflar sadece `_send` yazar.
   - Onay kapısı: son karar `approve` olmalı, platform seçilmiş olmalı, onaylayan yetkili
     Telegram ID'si olmalı, içerik onaydan sonra değişmemiş olmalı (SHA-256 özet).
   - Veritabanı: `publications.approval_id NOT NULL`, `UNIQUE(content_id, platform)`,
     `approval_records` tablosunda UPDATE/DELETE tetikleyiciyle engelli.
   - Şalterler: `PUBLISHING_ENABLED=false`, `DRY_RUN=true` varsayılandır.
   - Acil durdurma: Telegram'dan `/durdur` → yayın ve ücretli AI çağrıları anında durur.
   - Demo içerik hiçbir koşulda yayınlanamaz.
2. **Önce kural, sonra AI** — Ucuz kural tabanlı ön eleme (fiyat geçmişi, indirim oranı, stok)
   geçen ürünler AI'a gider. Aynı ürün+fiyat+kupon için `deals.fingerprint` ve `ai_cache`
   tekrar analizi engeller.
3. **Bütçe koruması** — Her ücretli çağrıdan önce `ensure_budget()`; limit aşılacaksa çağrı yapılmaz.
4. **Kesintiye dayanıklılık** — Servis başına devre kesici: ardışık hatalarda servis
   geçici olarak devre dışı kalır, kredi harcanmaz, sistem çökmez.
5. **Dürüst veri** — Mağazanın gösterdiği "eski fiyat" (`list_price`) ayrı tutulur; indirim
   yalnızca kendi fiyat geçmişimizle doğrulanırsa "doğrulanmış" sayılır.
6. **Düşük maliyet** — Tek süreç, SQLite, tek küçük VPS. PostgreSQL'e geçiş yalnızca
   `DATABASE_URL` değişikliğidir.

## Teknoloji seçimleri

| Konu | Seçim | Neden |
|---|---|---|
| Dil | Python 3.12 | AI/HTTP ekosistemi, Telegram kütüphaneleri, hızlı geliştirme |
| Veritabanı | SQLite (WAL) → PostgreSQL | Başlangıçta sıfır maliyet/bakım; SQLAlchemy ile aynı kod |
| Zamanlayıcı | APScheduler (süreç içi) | Ayrı kuyruk sunucusu (Redis/Celery) gerekmez |
| Dağıtım | Docker Compose, root olmayan kullanıcı | Tek komutla kurulum, otomatik yeniden başlama, healthcheck |
| Ayarlar | `.env` + pydantic-settings | Anahtarlar koda/Git'e girmez, `SecretStr` ile loglanmaz |
| n8n | Kullanılmıyor | İstendiği gibi; tüm otomasyon kodla |

## Modüller

| Modül | Görev | Faz |
|---|---|---|
| `sepetfy/app` | Önyükleme, loglama (gizli değer maskeleme), hata sınıfları, devre kesici | 1 ✅ |
| `sepetfy/config` | `.env` ayarları, kaynak kataloğu ve erişim değerlendirmesi | 1 ✅ |
| `sepetfy/database` | Şema, Alembic göçleri (açılışta otomatik), oturum, yedekleme, başlangıç verisi | 1 ✅ / 2 ✅ |
| `sepetfy/approval` | Onay kapısı, karar kayıtları, onay kartı (`preview.py`), Telegram yönetim botu (`bot.py`) — bkz. [TELEGRAM.md](TELEGRAM.md) | 1 ✅ / 5 ✅ |
| `sepetfy/publishers` | `BasePublisher` (zorunlu onay akışı); platform yayıncıları | 1 ✅ çekirdek / 6 |
| `sepetfy/analytics` | Maliyet defteri + bütçe (1 ✅); performans & rapor (7) | 1 / 7 |
| `sepetfy/scheduler` | 7/24 servis: heartbeat, günlük yedek, feed kontrolleri, bayat feed uyarısı | 1 ✅ / 2 ✅ |
| `sepetfy/collectors/manual.py` | Telegram'dan manuel ürün gözlemi (link + fiyat) | 5 ✅ |
| `sepetfy/collectors/feed` | Genel XML feed toplayıcı (koşullu indirme, akış ayrıştırma, sağlamlık kontrolü, partili eşitleme) — bkz. [FEEDS.md](FEEDS.md) | 2 ✅ |
| `sepetfy/price_tracker` | Son 30 günün en düşüğü kuralı, indirim doğrulama, şüpheli eski fiyat tespiti | 2 ✅ |
| `sepetfy/deal_scoring` | 100 puanlık puanlama, A/B/C sınıfı, gerekçeler | 3 |
| `sepetfy/ai` | Claude istemcisi (önbellek + bütçe korumalı) | 3 |
| `sepetfy/content` | Platform şablonları: Telegram gönderisi (AI'sız) ✅; diğerleri FAZ 3–4 | 5 ✅ / 3–4 |
| `sepetfy/influencer` | Sanal karakter tanımı ve tutarlılık kuralları | 4 |
| `sepetfy/media` | Gemini görsel, Kling video, dosya depolama | 4 |
| `sepetfy/integrations` | Telegram Bot API istemcisi ✅; Google Drive / Sheets, affiliate ağları (4+) | 5 ✅ / 4+ |
| `sepetfy/notifications` | Yöneticiye Telegram bildirimi ve tekrar sınırlı uyarılar (bütçe, feed, devre kesici) | 5 ✅ |

## Veri modeli (özet)

`stores` → `products` → `price_snapshots` (fiyat geçmişi); `feed_states` (feed başına son durum)
`deals` (puan, sınıf, gerekçe, parmak izi) → `content_items` (platform, sürüm, özet)
→ `approval_records` (append-only) → `publications` (UNIQUE içerik+platform)
→ `performance_metrics`
Yan tablolar: `cost_entries`, `ai_cache`, `app_settings`.

Şema değişiklikleri `sepetfy/database/migrations/versions/` altında Alembic göçleridir ve
uygulama açılışında otomatik uygulanır; mevcut veriler korunur. Göçler SQLite ve PostgreSQL'de test edilir.
