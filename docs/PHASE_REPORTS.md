# Faz Raporları

## FAZ 1 — Altyapı (2026-11-24) ✅

### Yapılanlar
- Modüler proje yapısı (`sepetfy/` altında 16 modül, her birinin görevi belgelendi).
- Veritabanı şeması (11 tablo): mağaza, ürün, fiyat geçmişi, fırsat, içerik, onay kaydı,
  yayın, maliyet, performans, AI önbelleği, ayarlar. SQLite (WAL) varsayılan, PostgreSQL uyumlu.
- `.env` tabanlı ayarlar; gizli değerler `SecretStr`, boş anahtar = "yapılandırılmadı".
- Loglama: dönen dosyalar (`logs/sepetfy.log`, `logs/errors.log`), gizli değer maskeleme.
- Hata yönetimi: hata sınıfları + servis başına devre kesici ve geri çekilmeli tekrar deneme.
- **Onay kapısı çekirdeği** ve `BasePublisher` (yayıncılar ileride bunun üzerine kurulacak).
- **Bütçe koruması**: maliyet defteri, aylık limit, %80 uyarı, limit aşımında durdurma.
- Yedekleme: tutarlı SQLite yedeği + gzip + rotasyon; günlük otomatik görev.
- Zamanlayıcı servisi (heartbeat + günlük yedek), SIGTERM ile temiz kapanış.
- Docker: root olmayan kullanıcı, healthcheck, kalıcı klasörler, isteğe bağlı PostgreSQL profili.
- Kaynak kataloğu: 10 kaynak, hepsi **kapalı** ve erişim durumu işaretli.

### Testler
- 26 otomatik test geçti: onay atlatma senaryoları (onaysız, yetkisiz kullanıcı, seçilmemiş
  platform, sonradan red, onay sonrası metin değişikliği, şalter kapalı, çift yayın — hem
  uygulama hem veritabanı seviyesinde, onay kaydı silme/değiştirme), log maskeleme,
  devre kesici, bütçe, yedekle-geri yükle, ayar ayrıştırma.
- CLI komutlarının tamamı elle çalıştırıldı.
- Docker imajı derlendi; `docker compose` ile konteyner `healthy` durumuna geldi.

### Test sırasında bulunup düzeltilen hatalar
1. Docker `--env-file` satır içi yorumları desteklemiyor → `.env.example` yorumları ayrı satıra taşındı.
2. Compose'daki zorunlu `POSTGRES_PASSWORD` kontrolü, postgres profili kapalıyken bile
   başlatmayı engelliyordu → düzeltildi.
3. Bind-mount klasörleri root'a ait olunca root olmayan konteyner log yazamıyordu →
   `scripts/prepare_dirs.sh` eklendi.

### Eksikler / bilinçli olarak sonraya bırakılanlar
- Telegram bütçe uyarısı bildirimi: uyarı şu an loga yazılıyor; Telegram'a gönderim FAZ 5.
- Veritabanı göç aracı (Alembic): şema ilk kez değiştiğinde (FAZ 2) eklenecek. → FAZ 2’de eklendi ✅
- Hiçbir harici servise bağlantı yok (beklenen: henüz anahtar yok).

### Maliyet
- FAZ 1 çalışma maliyeti: **$0** (ücretli API çağrısı yok).

## FAZ 2 — Fırsat Takibi, 1. bölüm: Genel XML feed toplayıcı (2026-11-24) 🔄

### Yapılanlar
- **Feed toplayıcı:** koşullu indirme (ETag / Last-Modified / SHA-256), gzip/zip, boyut sınırı,
  URL'nin (token) hiçbir hata mesajına/loga yazılmaması.
- **Güvenli XML ayrıştırma** (`defusedxml`: XXE ve entity bombasına karşı), akış hâlinde okuma.
- **Türkçe fiyat biçimleri** (`1.299,90 TL`, `₺12.500`, `1299.90 TRY` ...) ve stok ifadeleri.
- **JSON alan eşleştirme:** yeni feed = bir JSON dosyası. Hepsiburada için Google Shopping
  biçiminde taslak hazır (kapalı; gerçek feed görülünce düzeltilecek).
- **Sağlamlık kontrolü:** bozuk XML, ani ürün sayısı düşüşü, bozuk eşleştirme -> reddet,
  veritabanına dokunma, son sağlam veriyle devam et.
- **Partili eşitleme:** yalnızca değişiklikler fiyat geçmişine yazılır; kaybolan ürün 3 kontrolde pasife alınır.
- **İndirim doğrulama:** son 30 günün en düşük fiyatı kuralı, 7 gün asgari geçmiş, şüpheli eski fiyat işareti,
  tekrar fırsat önleme.
- **Zamanlayıcı:** feed başına iş + saatlik bayat feed kontrolü. CLI: `feeds`, `feed-run`, `feed-import`.
- **Alembic şema göçleri:** açılışta otomatik; FAZ 1 veritabanları veri kaybı olmadan yükseltilir.

### Testler
- SQLite: 95 test geçti (5'i yalnızca PostgreSQL'de çalışır).
- **PostgreSQL 16: 100 testin tamamı geçti** (geçici bir PostgreSQL konteynerinde).
- Yük testi: 50.000 ürün 11 sn / 114 MB; 200.000 ürün 46 sn / 150 MB.
- Docker: FAZ 1'de oluşturulmuş gerçek veritabanı yeni imajla `0001 -> 0002` yükseltildi, veriler korundu.

### Test sırasında bulunup düzeltilen hatalar
1. İlk sürüm 50.000 ürünlük feed'de **555 MB** bellek kullanıyordu -> iki geçişli akış + partili yazma (114 MB).
2. FAZ 1 veritabanında yeni sütunlar yoktu (`no such column`) -> Alembic göçleri eklendi.
3. Otomatik üretilen göç, dolu tabloya varsayılansız `NOT NULL` sütun ekliyordu ve SQLite'ta
   tabloyu yeniden kurmaya çalışıyordu -> elle düzeltildi.
4. **SQLite sürücüsü tablo işlemlerini (DDL) geri alınabilir işleme dahil etmiyordu**; yarıda kalan bir
   göç veritabanını tutarsız bırakmıştı -> gerçek işlem yönetimi açıldı, yarım kalmış veritabanı
   kurtarma desteği ve testleri eklendi.
5. Zamanlayıcıda URL'si olmayan feed işi kalıcı olarak duraklatılıyordu -> düzeltildi.
6. Docker imajına göç dosyaları dahil edilmiyordu (paket tanımı eksikti) -> düzeltildi, imajda doğrulandı.
7. Sağlık kontrolü her dakika göç kontrolü yapıp log kirletiyordu -> kaldırıldı.

### Eksikler
- **Gerçek feed bağlantısı yok**: affiliate onayı ve feed URL'si bekleniyor. Eşleştirme taslak.
- Manuel link girişi (Telegram'dan ürün linki) FAZ 5 Telegram botuyla birlikte gelecek.
- Bayat feed ve reddedilen feed uyarıları şimdilik loga yazılıyor (Telegram: FAZ 5).
- Kupon doğrulama: feed'deki kupon "doğrulanmamış" olarak kaydediliyor; ağ kupon feed'i sunarsa eklenecek.

### Maliyet
- **$0.** Feed takibinde AI kullanılmıyor.
