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
- Veritabanı göç aracı (Alembic): şema ilk kez değiştiğinde (FAZ 2) eklenecek.
- Hiçbir harici servise bağlantı yok (beklenen: henüz anahtar yok).

### Maliyet
- FAZ 1 çalışma maliyeti: **$0** (ücretli API çağrısı yok).
