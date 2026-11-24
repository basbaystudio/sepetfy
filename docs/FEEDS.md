# Ürün Feed'leri (XML) — Nasıl Çalışır, Nasıl Eklenir

## Akış

```
Zamanlayıcı (feed başına, ör. 60 dk)
  1. Değişti mi?   ETag / Last-Modified (sunucu "aynı" derse hiç indirme yok)
                   + SHA-256 içerik özeti (sunucu desteklemiyorsa)
  2. İndir         Diske parça parça, boyut sınırı, gzip/zip açma. URL loglara yazılmaz.
  3. Kontrol et    1. geçiş: sadece sayar. Bozuk XML / ürün sayısında ani çöküş /
                   eşleştirme bozuk -> REDDEDİLİR, veritabanına dokunulmaz.
  4. Eşitle        2. geçiş: 2.000'lik partilerle. Sadece değişen fiyat/stok/kupon
                   yeni fiyat kaydı oluşturur. Feed'den 3 kez kaybolan ürün pasife alınır.
  5. Fırsat adayı  Fiyatı düşenler: son 30 günün en düşük fiyatına göre doğrulanmış
                   düşüş >= %10 ve stokta -> fırsat adayı (puanlama FAZ 3).
```

Ölçümler (bu geliştirme ortamında, SQLite):

| Feed | İlk yükleme | %5 fiyat değişimi | Bellek |
|---|---|---|---|
| 50.000 ürün (5,7 MB) | 11 sn | 8 sn | 114 MB |
| 200.000 ürün (23 MB) | 46 sn | 32 sn | 150 MB |

İçerik değişmediğinde 1. adımda durulur; bu süreler yalnızca feed gerçekten değiştiğinde yaşanır.

## İndirim doğrulama kuralları

| Kural | Neden |
|---|---|
| Referans = **son 30 günün en düşük fiyatı** (bizim gözlemimiz) | "Önce fiyatı şişir, sonra indirim yap" hilesini etkisiz kılar |
| En az **7 gün** izlenmemiş ürünün indirimi "doğrulanmadı" sayılır | Yeni eklenen ürünlerde geçmiş yok |
| Mağazanın "eski fiyat"ı, son 30 günde gördüğümüz en yüksek fiyattan %5+ yüksekse **"şüpheli eski fiyat"** işareti | Sahte indirim tespiti |
| Stokta olmayan ürün fırsat olamaz | — |
| Aynı ürün + fiyat + kupon için ikinci aday oluşmaz | Tekrar analiz ve AI maliyeti önlenir |

**Sonuç:** Sistem ilk kurulduğunda ilk 7 gün "doğrulanmış indirim" üretmez. Bu bilinçli bir tercih:
doğrulanamayan indirim, indirim olarak tanıtılmaz.

## Yeni bir feed eklemek

1. Affiliate panelinden **feed URL'sini** alın. Sohbete yapıştırmayın; `.env` dosyasına ekleyin:
   `HEPSIBURADA_FEED_URL=https://...`
2. Feed'den küçük bir örnek (ilk birkaç ürün) paylaşın. Token içermeyen bir **ürün örneği** yeterli.
   Ben `sepetfy/config/feeds/<ad>.json` alan eşleştirmesini yazıp testini eklerim.
3. Elle deneme: `python -m sepetfy feed-run hepsiburada`
4. Sonuç doğruysa JSON'da `"enabled": true` yapılır; zamanlayıcı otomatik çalıştırır.

### Alan eşleştirme örneği

```json
{
  "name": "hepsiburada", "store_code": "hepsiburada", "enabled": true,
  "url_env": "HEPSIBURADA_FEED_URL", "interval_min": 60, "item_tag": "item",
  "fields": {
    "external_id": "g:id", "title": "g:title", "url": "g:link",
    "price": ["g:sale_price", "g:price"], "list_price": "g:price",
    "in_stock": "g:availability", "image_url": "g:image_link"
  }
}
```

Yol yazımı: `"fiyat/satis"` iç içe etiket, `"@kod"` öznitelik, `["a","b"]` ilk dolu olanı alır.
Zorunlu alanlar: `external_id`, `title`, `url`, `price`.
Fiyat biçimi otomatik algılanır (`1.299,90 TL`, `1299.90 TRY`); gerekirse `"decimal": "comma"|"dot"`.

## URL'siz feed (sadece "İndir" butonu varsa)

`python -m sepetfy feed-import hepsiburada indirilen.xml` — aynı kontrol ve eşitleme adımlarından geçer.
(FAZ 5'te dosyayı Telegram botuna göndermek de yeterli olacak.)

## Komutlar

| Komut | Açıklama |
|---|---|
| `python -m sepetfy feeds` | Feed'ler, URL durumu, son başarı zamanı, ürün sayısı, son hata |
| `python -m sepetfy feed-run AD` | Feed'i şimdi kontrol et |
| `python -m sepetfy feed-import AD DOSYA` | Elle indirilmiş XML'i işle |

## İzleme

- Feed 24 saattir başarıyla güncellenmediyse saatlik kontrol uyarı loglar (FAZ 5: Telegram uyarısı).
- Her çalıştırmanın özeti (`okunan / yeni / değişen / reddedilen / fırsat`) `feed_states` tablosunda tutulur.
