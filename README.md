# Sepetfy

Türkiye'deki gerçek indirimleri bulan, sahte indirimleri eleyen, AI ile platforma özel
içerik hazırlayan ve **yalnızca sizin onayınızla** çok kanallı yayın yapan otomasyon sistemi.
n8n kullanılmaz; tüm otomasyon Python ile kodlanmıştır.

## Durum

| Faz | Kapsam | Durum |
|---|---|---|
| 1 | Altyapı: veritabanı, ayarlar, loglama, hata yönetimi, onay kapısı çekirdeği, bütçe koruması, yedekleme, Docker | ✅ Tamamlandı, test edildi |
| 2 | Fırsat takibi: genel XML feed toplayıcı ✅, fiyat geçmişi ✅, indirim doğrulama ✅; gerçek feed bağlantısı ⏳ (affiliate onayı bekleniyor), manuel link girişi ⏳ | 🔄 Devam ediyor |
| 3 | Claude analizi, puanlama, içerik üretimi | — |
| 4 | Gemini görsel, Kling video, AI influencer | — |
| 5 | Telegram yönetim botu: onay kartları, komutlar, acil durdurma, uyarılar, manuel ürün, XML yükleme | ✅ Kodlandı ve test edildi · ⏳ sizin bot token'ınızla canlı test |
| 6 | Yayın: Telegram → Instagram → TikTok → Pinterest → diğerleri | — |
| 7 | Performans, maliyet raporları, optimizasyon | — |

## Hızlı başlangıç

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
.venv/bin/python -m sepetfy init-db
.venv/bin/python -m sepetfy check
.venv/bin/python -m pytest
```

Docker / VPS: [docs/SETUP.md](docs/SETUP.md)

## Dokümanlar

- [Mimari ve modüller](docs/ARCHITECTURE.md)
- [Kaynak ve platform erişim değerlendirmesi](docs/SOURCES_AND_PLATFORMS.md)
- [Ürün feed'leri: çalışma şekli ve yeni feed ekleme](docs/FEEDS.md)
- [Telegram yönetim botu: kurulum ve kullanım](docs/TELEGRAM.md)
- [Tahmini maliyet](docs/COSTS.md)
- [Kurulum](docs/SETUP.md)
- [Faz raporları](docs/PHASE_REPORTS.md)

## Değişmez kural

Sizin açık onayınız olmadan hiçbir içerik yayınlanmaz. Bu kural uygulama, onay kapısı
ve veritabanı seviyesinde ayrı ayrı zorunludur ve testlerle doğrulanır
(`tests/test_approval_guard.py`).
