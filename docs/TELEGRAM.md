# Telegram Yönetim Botu — Kurulum ve Kullanım

Bu bot **yalnızca sizin** kullanımınız içindir: onay merkezi, fırsat bildirimleri, ürün ekleme,
uzaktan kontrol ve uyarılar. Takipçilere yayın yapılacak Sepetfy kanalı ayrı bir konudur (FAZ 6).

## Kurulum (yaklaşık 5 dakika)

### 1. Botu oluşturun
1. Telegram'da **@BotFather**'ı açın (mavi onay işaretli resmî hesap).
2. `/newbot` yazın.
3. Görünen ad: örn. `Sepetfy Yönetim`
4. Kullanıcı adı: `bot` ile bitmeli, örn. `sepetfy_yonetim_bot` (alınmışsa başka bir ad deneyin).
5. BotFather size `123456789:AA...` biçiminde bir **token** verir.
   ⚠️ Bu token botun şifresidir. **Sohbete, e-postaya, ekran görüntüsüne koymayın.**

### 2. Token'ı `.env` dosyasına yazın
```
TELEGRAM_BOT_TOKEN=123456789:AA...
```

### 3. Kendi kullanıcı kimliğinizi öğrenin
İki yol var:
- **Kolay:** `python -m sepetfy bot` çalıştırın, Telegram'da botunuzu açıp `/start` yazın.
  Yönetici tanımlı olmadığı için bot yalnızca kimliğinizi söyler, başka hiçbir şey yapmaz.
- **Alternatif:** **@userinfobot**'a herhangi bir mesaj yazın; size `Id: 123456789` döner.

`.env` dosyasına ekleyin:
```
TELEGRAM_ADMIN_USER_IDS=123456789
```
(Birden fazla yönetici: `111,222`)

### 4. Test edin
```bash
python -m sepetfy telegram-test
```
"Deneme mesajı gönderildi" görürseniz ve Telegram'a mesaj geldiyse kurulum tamam.
Mesaj gelmediyse botu Telegram'da açıp bir kez `/start` yazın (botlar size ilk mesajı atamaz).

### 5. Çalıştırın
- Sürekli çalışma (VPS / Docker): `docker compose up -d --build` — bot, zamanlayıcıyla aynı serviste başlar.
- Yalnızca bot (deneme için): `python -m sepetfy bot`
- Sonra Telegram'da `/demo` yazıp butonları deneyin. Demo içerik **hiçbir koşulda yayınlanamaz.**

> Aynı bot token'ı ile aynı anda yalnızca **bir** kopya çalışmalı (Telegram 409 hatası verir).
> VPS'te çalışırken bilgisayarınızda ayrıca `python -m sepetfy bot` çalıştırmayın.

## Kullanım

### Onay kartı
Her fırsat için: ürün görseli, fiyat, doğrulanmış indirim, uyarılar (ör. şüpheli eski fiyat), bağlantı,
hazırlanan içerikler ve butonlar gelir:

| Buton | Ne olur |
|---|---|
| ☑ / ☐ Platform | Hangi platformlarda yayınlanacağını seçer |
| ✅ ONAYLA | Yalnızca seçili platformlar için onay kaydı. Seçilmeyenler reddedilir |
| ❌ REDDET | Tüm içerikler reddedilir |
| ✏️ DÜZENLE | Not istenir; içerik yayınlanamaz hâle gelir. Notla yeniden yazım Claude (FAZ 3) ile gelecek |
| ⏰ SONRA | 3 saat sonra kart tekrar gönderilir |

Her karar silinemez şekilde kaydedilir (kim, ne zaman, hangi platform, hangi içerik sürümü).
Bir karta ikinci kez basılırsa "karar zaten verildi" denir.

**Şu an:** Onay kaydedilir ama **hiçbir şey yayınlanmaz**. Platform yayıncıları FAZ 6'da eklenecek.

### Ürün ekleme (manuel)
Ürün linkini gönderin, bot adını ve fiyatını sorar. Ya da tek mesajda:
```
https://www.hepsiburada.com/...
Kablosuz Kulaklık X100
1.199,90
1.499,90        ← (isteğe bağlı) mağazanın gösterdiği eski fiyat
```
Sepetfy mağaza sayfasını kendisi okumaz (kullanım şartları); fiyatı siz girersiniz. Aynı linki
ileride yeni fiyatla tekrar gönderdikçe fiyat geçmişi oluşur. **En az 7 günlük geçmiş** ve son 30 günün
en düşük fiyatına göre en az %10 düşüş olduğunda fırsat kartı otomatik gelir.

### XML feed dosyası
`.xml`, `.xml.gz` veya `.zip` dosyasını bota gönderin (Telegram bot sınırı: **20 MB**).
Feed kontrol edilir, işlenir; fırsat adayları kart olarak gelir.

### Komutlar
| Komut | Açıklama |
|---|---|
| `/durum` | Sistem, onay bekleyenler, bütçe, feed'ler, entegrasyonlar |
| `/bekleyenler` | Onay bekleyen kartları tekrar göster |
| `/firsatlar [n]` | Onaya sunulmamış yeni fırsatlardan en iyi n tanesini gönder |
| `/maliyet` | Son 24 saat / bu ay / ay sonu tahmini |
| `/butce [USD]` | Aylık AI bütçesini göster / değiştir |
| `/feeds` | Feed'lerin son durumu ve hataları |
| `/durdur` | **Acil durdurma:** yayın ve ücretli AI çağrıları anında durur (fiyat takibi sürer) |
| `/baslat` | Acil durdurmayı kaldırır (onay ister) |
| `/demo` | Deneme kartı (yayınlanamaz) |
| `/iptal` | Yarım kalan işlemi iptal eder |

### Otomatik uyarılar
| Uyarı | Tekrar aralığı |
|---|---|
| Bütçenin %80'i kullanıldı | günde en fazla 1 |
| Bütçe sınırı: ücretli çağrı engellendi | 12 saatte 1 |
| Feed indirilemedi / reddedildi | feed başına 6 saatte 1 |
| Feed 24 saattir güncellenmedi | feed başına günde 1 |
| Bir servis art arda hata verdi (devre kesici) | saatte 1 |

## Güvenlik
- Bot yalnızca `TELEGRAM_ADMIN_USER_IDS` içindeki hesaplardan, **özel sohbette** gelen mesajları işler.
  Grup içinden gelen komutlar (sizden bile olsa) ve başkalarının mesajları yok sayılır.
- Token yalnızca `.env` dosyasındadır; loglarda ve hata mesajlarında maskelenir.
- Güncellemeler "en fazla bir kez" işlenir: servis yeniden başlasa bile bir butona iki kez basılmış sayılmaz.
- Token sızdığından şüphelenirseniz: @BotFather → `/revoke` → yeni token'ı `.env`'ye yazın.

## Sınırlar
- Bot size ilk mesajı atamaz; bir kez `/start` yazmanız gerekir.
- Bot başka kanalların mesajlarını okumaz.
- Dosya yükleme sınırı 20 MB (daha büyük feed'ler için URL kullanılır).
- Bot, servis çalıştığı sürece yanıt verir; 7/24 için VPS gerekir.
