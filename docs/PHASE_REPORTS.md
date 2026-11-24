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

## FAZ 5 — Telegram Yönetim Botu (2026-11-25) ✅ kodlandı · ⏳ canlı test

FAZ 3–4'ten önce yapıldı: ücretsiz, başvuru gerektirmiyor ve onay mekanizması her şeyin ön koşulu.

### Yapılanlar
- **Telegram Bot API istemcisi** (`httpx`, yeni bağımlılık yok); token hiçbir hata mesajına/loga sızmaz.
- **Onay kartı:** görsel, fiyatlar, doğrulanmış indirim, şüpheli eski fiyat uyarısı, bağlantı, içerikler;
  platform seçimi + ONAYLA / REDDET / DÜZENLE / SONRA. Kararlar onay kapısından (append-only) kaydedilir.
- **Telegram gönderi şablonu (AI'sız):** yalnızca doğrulanmış indirim yazılır; affiliate açıklaması ve
  "fiyat değişebilir" uyarısı her gönderide var; HTML güvenli.
- **Komutlar:** /durum /bekleyenler /firsatlar /maliyet /butce /feeds /durdur /baslat /demo /iptal /yardim
- **Acil durdurma:** /durdur → yayın kapısı ve ücretli AI çağrıları (bütçe koruması) anında kilitlenir.
- **Manuel ürün girişi:** link → ad → fiyat → eski fiyat (veya tek mesaj); aynı link tekrarlandıkça fiyat geçmişi.
- **XML yükleme:** dosyayı bota gönder → feed hattından geçer → fırsat kartları.
- **Uyarılar** (tekrar sınırlı): bütçe %80 / sınır, feed hatası, bayat feed, devre kesici.
- **SONRA hatırlatması** (3 saat); toplu önizleme sınırı (`TELEGRAM_MAX_PREVIEWS`, varsayılan 5).
- **Güvenilirlik:** güncellemeler en fazla bir kez işlenir; Telegram'a ulaşılamazsa artan aralıklarla
  yeniden dener ve sistem çalışmaya devam eder; geçersiz token (401) ve çift çalışma (409) açıkça loglanır.
- **Kurulum modu:** yönetici tanımlı değilken bot yalnızca kullanıcıya kendi kimliğini söyler.
- CLI: `telegram-test`, `bot`. Docker'da bot, zamanlayıcıyla aynı serviste başlar.

### Testler
- 31 bot testi: yabancı kullanıcı ve grup sohbeti yok sayılır, yetkisiz buton, tüm onay akışları,
  çift basma, demo içeriğin yayınlanamaması, acil durdurmanın bütçe ve yayın kapısını kilitlemesi,
  manuel ürün konuşması, XML yükleme, uyarı tekrar sınırı, token sızıntısı, Telegram'ın 64 bayt buton sınırı
  ve Telegram'ı taklit eden sunucuya karşı **gerçek HTTP istemcisi + uzun sorgulama döngüsüyle uçtan uca** /demo → ONAYLA.
- Tüm paket: SQLite 126 (+5 yalnız PG), **PostgreSQL 16'da 131/131**.
- Docker: token yokken ve Telegram'a ulaşılamazken servis sağlıklı kaldı; token loglarda yok.

### Geliştirme sırasında bulunup düzeltilen hatalar
1. Önizleme kodu modelde olmayan bir alana (`Deal.snapshot`) erişiyordu; çalışınca çökerdi → düzeltildi.
2. Offset testi kendi kendini doğruluyordu (hiçbir şey kanıtlamıyordu) → gerçek güncelleme kimliğiyle yeniden yazıldı.
3. Devre kesici uyarı dinleyicisi her açılışta yeniden eklenebiliyordu → modül seviyesine taşındı.

### Eksikler / dürüst notlar
- **Gerçek Telegram ile canlı test yapılamadı:** bu geliştirme ortamının ağ kuralları api.telegram.org'a
  izin vermiyor (bot token'ı da buraya girmemeli). İlk canlı test sizin kurulumunuzda `telegram-test` ile.
- Onaylanan içerik henüz **yayınlanmıyor** (yayıncılar FAZ 6).
- DÜZENLE notu kaydediliyor ama notla otomatik yeniden yazım Claude ile (FAZ 3) gelecek.
- Fırsat puanı (A/B/C) henüz yok; kartlar doğrulanmış indirim oranına göre sıralanıyor (FAZ 3).

### Maliyet
- **$0.** Telegram Bot API ücretsiz; AI kullanılmıyor.
