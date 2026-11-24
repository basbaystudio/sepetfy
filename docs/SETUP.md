# Kurulum

## A) Yerel geliştirme (Linux / macOS / WSL)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env && chmod 600 .env      # değerleri düzenleyin
.venv/bin/python -m sepetfy init-db
.venv/bin/python -m sepetfy check
.venv/bin/python -m pytest
```

## B) VPS (7/24)

1. Ubuntu 24.04 VPS açın (2 vCPU / 4 GB yeterli). SSH anahtarı ile girin, parola girişini kapatın.
2. Docker kurun: `curl -fsSL https://get.docker.com | sh`
3. Projeyi çekin: `git clone <repo> sepetfy && cd sepetfy`
4. Klasörleri ve `.env` dosyasını hazırlayın: `./scripts/prepare_dirs.sh`
5. `.env` dosyasını düzenleyin: `nano .env` (anahtarları **sohbete değil**, buraya yazın)
6. Başlatın: `docker compose up -d --build`
7. Kontrol: `docker compose ps` (durum `healthy` olmalı) ve
   `docker compose exec app python -m sepetfy check`

## Komutlar

| Komut | Açıklama |
|---|---|
| `python -m sepetfy init-db` | Tabloları oluşturur, kaynak kataloğunu yükler (idempotent) |
| `python -m sepetfy check` | Sağlık ve yapılandırma durumu (gizli değer göstermez) |
| `python -m sepetfy sources` | Kaynakların erişim durumu |
| `python -m sepetfy backup` | Anlık yedek (`backups/`, son 14 tutulur) |
| `python -m sepetfy costs` | Günlük/aylık maliyet özeti |
| `python -m sepetfy set-budget 30` | Aylık bütçe limiti (USD) |
| `python -m sepetfy run` | Zamanlayıcı servisi |
| `python -m sepetfy telegram-test` | Bot token'ını doğrular, yöneticilere deneme mesajı gönderir |
| `python -m sepetfy bot` | Yalnızca Telegram botunu çalıştırır (deneme) |
| `python -m sepetfy feeds` | Ürün feed'lerinin durumu |
| `python -m sepetfy feed-run AD` | Bir feed'i şimdi kontrol et |
| `python -m sepetfy feed-import AD DOSYA` | Elle indirilmiş XML'i işle |

## Yedekleme ve geri yükleme

- Otomatik: her gün 04:17'de (İstanbul saati) `backups/sepetfy-*.db.gz`.
- Sunucu dışına kopyalamanız önerilir (ör. haftalık `scp` veya Drive — FAZ 4).
- Geri yükleme (servis durdurulmuşken):
  ```bash
  docker compose stop app
  gunzip -c backups/sepetfy-YYYYMMDDTHHMMSSZ.db.gz > data/sepetfy.db
  docker compose start app
  ```

## Şema güncellemeleri

Yeni sürüm kurulduğunda (`docker compose up -d --build`) veritabanı şeması açılışta otomatik
yükseltilir, veriler korunur. Yükseltmeden önce `python -m sepetfy backup` almanız önerilir.

## Testleri PostgreSQL üzerinde çalıştırmak

```bash
SEPETFY_TEST_DB_URL=postgresql+psycopg://... SEPETFY_TEST_PG_URL=postgresql+psycopg://... pytest
```

## PostgreSQL'e geçiş (gerektiğinde)

`.env`'de `POSTGRES_PASSWORD` ve `DOCKER_DATABASE_URL` tanımlayın, sonra
`docker compose --profile postgres up -d --build`. Veri taşıma betiği geçiş anında yazılacak.

## Güvenlik notları

- `.env` Git'e eklenmez (`.gitignore`), izni `600` olmalıdır.
- Konteyner root olmayan kullanıcıyla (uid 10001) çalışır; imaja gizli bilgi gömülmez.
- Loglarda bilinen anahtar biçimleri ve `.env`'deki gerçek değerler maskelenir.
- Telegram komutları yalnızca `TELEGRAM_ADMIN_USER_IDS` içindeki hesaplardan kabul edilir (FAZ 5).
