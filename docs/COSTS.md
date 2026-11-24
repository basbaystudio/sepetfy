# Tahmini Aylık Maliyet

> **Claude Code aboneliği ≠ Claude API.** Claude Code aboneliği bu projeyi *geliştirmek*
> için kullanılır. Sistemin 7/24 çalışırken yaptığı Claude çağrıları **ayrı** olarak
> console.anthropic.com hesabınızdan, token başına faturalanır.
>
> Rakamlar tahmindir; Gemini ve Kling fiyatları ilgili fazda resmî fiyat sayfasından
> yeniden doğrulanacaktır. Gerçek harcama `python -m sepetfy costs` ile izlenir.

## Varsayımlar

- Kural tabanlı ön eleme sonrası günde ~40 ürün AI analizine gider (aynı ürün tekrar analiz edilmez).
- Günde ~15 fırsat için platform içerikleri üretilir.
- Günde ~10 görsel (yalnızca A/B sınıfı), günde ~2 video (yalnızca A sınıfı).

## Claude API (model seçimi sizin kararınız; `.env` → `CLAUDE_MODEL`)

| Model | Fiyat (girdi / çıktı, 1M token) | Analiz (~40/gün) | İçerik (~15/gün) | Aylık |
|---|---|---|---|---|
| Claude Opus 5.5 (varsayılan) | $4 / $20 | ~$31 | ~$32 | **~$60** |
| Claude Sonnet 5.5 | $2 / $10 | ~$16 | ~$16 | **~$30** |
| Claude Haiku 5.5 | $0.10 / $0.50 | ~$1 | ~$1 | **~$2** |

Ek tasarruf: sistem promptu önbellekleme (prompt caching) ve acil olmayan işler için
Batch API (%50 indirim) FAZ 3'te uygulanacak.

## Diğer kalemler

| Kalem | Tahmin / ay | Not |
|---|---|---|
| VPS (2 vCPU, 4 GB, ör. Hetzner CX22 sınıfı) | ~$5–7 | 7/24 çalışma |
| Gemini görsel (~300 görsel) | ~$10–15 | Görsel başına birkaç sent; doğrulanacak |
| Kling video (~60 video) | ~$10–40 | Ön ödemeli kredi; mod/süreye göre değişir; doğrulanacak |
| Google Drive | $0 | 15 GB ücretsiz; gerekirse yerel disk + yedek |
| Telegram, Instagram, Pinterest API | $0 | |
| X API | ücretli | İsteğe bağlı, en son |

## Senaryolar

| Senaryo | İçerik | Aylık |
|---|---|---|
| Asgari | Haiku, görsel yok, video yok, sadece Telegram | **~$7–10** |
| Dengeli | Sonnet, günde ~10 görsel, video yok | **~$45–55** |
| Tam | Opus, görsel + günde ~2 video | **~$90–130** |

Bütçe koruması: `MONTHLY_BUDGET_USD` (varsayılan $30) aşılacaksa ücretli çağrılar durur,
%80'de Telegram uyarısı gelir. Limit `python -m sepetfy set-budget <USD>` ile değişir.
