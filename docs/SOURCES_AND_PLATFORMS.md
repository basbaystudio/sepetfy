# Veri Kaynakları ve Yayın Platformları — Erişim Değerlendirmesi

> Bu tablo başlangıç değerlendirmesidir. Her kaynak/platform, ilgili fazda **güncel resmî
> dokümana göre yeniden doğrulanacak** ve doğrulanmadan otomatikmiş gibi gösterilmeyecektir.
> Kullanım şartlarına aykırı scraping, giriş engeli aşma, rate-limit atlatma yapılmaz.

## Veri kaynakları

| Kaynak | Muhtemel resmî yol | Durum | Not |
|---|---|---|---|
| Trendyol | Affiliate/influencer programı veya affiliate ağı feed'i | Hesap gerekli | Satıcı API'si sadece kendi ürünleri içindir |
| Hepsiburada | Affiliate programı / ağ feed'i | Hesap gerekli | |
| Amazon TR | Amazon Ortaklık Programı + ürün API'si | Hesap gerekli | API erişimi genelde nitelikli satış şartına bağlı |
| N11, MediaMarkt, Teknosa, Boyner | Affiliate ağları | Doğrulanmadı | Hangi ağda feed sunduklarına bakılacak |
| Zara ve benzeri | Bilinen açık API yok | Manuel | Otomatik toplama yapılmaz |
| Marka kampanya sayfaları | RSS / bülten | Doğrulanmadı | robots.txt + şartlar kontrol edilir |
| **Manuel giriş** | Siz Telegram'dan ürün linki gönderirsiniz | **Hazır olacak (FAZ 2/5)** | Hesap beklerken çalışan ilk yol |

**Kontrol sıklığı:** Varsayılan 15 dk; kaynak bazında `stores.check_interval_min`.
Feed'ler genellikle saatlik/günlük güncellenir — bu durumda "anlık" değil, feed'in
güncellenme sıklığında yakalanır ve bu size açıkça raporlanır.

## Yayın platformları

| Platform | Resmî otomatik yayın | Gerekenler | Plan |
|---|---|---|---|
| Telegram kanalı | ✅ Bot API (ücretsiz) | Bot token, bot kanalda yönetici | FAZ 6'da ilk |
| Instagram | ✅ Graph API içerik yayınlama | Business/Creator hesap + Facebook Sayfası + Meta uygulaması | Görsel, Reels, hikâye |
| TikTok | ⚠️ Content Posting API | Uygulama denetimi (audit) olmadan gönderiler yalnızca **gizli** olur | Alternatif: videoyu TikTok taslaklarınıza (inbox) gönder, son dokunuşu siz yapın |
| Pinterest | ✅ API v5 pin oluşturma | Geliştirici uygulaması, önce deneme erişimi → standart erişim başvurusu | |
| WhatsApp Kanalı | ❌ Kanallara resmî yayın API'si bilinmiyor | — | Onaylanan içerik size "kopyala-yapıştır paketi" olarak gelir |
| X | ✅ API (ücretli) | Ücretli API planı | İsteğe bağlı, en son |
