# Windows'ta Kurulum (deneme / geliştirme)

Bu kurulum, sistemi ve Telegram botunu kendi bilgisayarınızda denemek içindir.
Bilgisayar kapalıyken veya uykudayken sistem çalışmaz; 7/24 için VPS'e geçilecek (docs/SETUP.md).

Tüm komutlar **PowerShell**'de çalıştırılır (Başlat → "PowerShell" yazın).
Her komutu tek tek kopyalayıp yapıştırın ve Enter'a basın.

## 1. Python ve Git'i kurun (bir kez)

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
```

Kurulum bitince **PowerShell penceresini kapatıp yeniden açın** (yeni programların tanınması için).
Kontrol:

```powershell
py -3.12 --version
git --version
```

> `winget` bulunamadı derse: Microsoft Store'dan "App Installer" (Uygulama Yükleyicisi) güncelleyin,
> ya da python.org ve git-scm.com'dan indirip kurun (Python kurulumunda **"Add python.exe to PATH"** işaretli olsun).

## 2. Projeyi indirin

```powershell
cd $HOME\Documents
git clone -b claude/sepetfy-ai-automation-k8o5ow https://github.com/basbaystudio/sepetfy.git
cd sepetfy
```

Depo özelse GitHub giriş penceresi açılır; hesabınızla giriş yapın.

## 3. Sanal ortam ve paketler

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

> Sanal ortamı "aktive etmiyoruz"; komutları doğrudan `.\.venv\Scripts\python.exe` ile çalıştırıyoruz.
> Böylece PowerShell'in "betik çalıştırma devre dışı" hatasıyla uğraşmanız gerekmez.

## 4. Ayar dosyası

```powershell
Copy-Item .env.example .env
notepad .env
```

Şimdilik hiçbir şeyi değiştirmeden kaydedip kapatabilirsiniz. Kontrol:

```powershell
.\.venv\Scripts\python.exe -m sepetfy init-db
.\.venv\Scripts\python.exe -m sepetfy check
```

`Veritabanı : sqlite -> OK` görmelisiniz.

## 5. Telegram botu

Ayrıntılı açıklama: [TELEGRAM.md](TELEGRAM.md)

1. Telegram'da **@BotFather** → `/newbot` → ad ve kullanıcı adı (sonu `bot` ile bitmeli).
2. Verilen token'ı `notepad .env` ile açıp şu satıra yazın, kaydedin:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AA...
   ```
   ⚠️ Token'ı sohbete, e-postaya veya ekran görüntüsüne koymayın.
3. Botu başlatın:
   ```powershell
   .\.venv\Scripts\python.exe -m sepetfy bot
   ```
4. Telegram'da botunuzu açın ve `/start` yazın. Bot size **kullanıcı kimliğinizi** söyler.
5. PowerShell'de **Ctrl+C** ile botu durdurun (en fazla ~30 saniye sürebilir).
6. `notepad .env` → şu satıra kimliğinizi yazın, kaydedin:
   ```
   TELEGRAM_ADMIN_USER_IDS=123456789
   ```
7. Test:
   ```powershell
   .\.venv\Scripts\python.exe -m sepetfy telegram-test
   ```
   Telegram'a "bağlantı testi başarılı" mesajı gelmeli.
8. Botu tekrar başlatın ve Telegram'da `/demo` yazın; kartın butonlarını deneyin:
   ```powershell
   .\.venv\Scripts\python.exe -m sepetfy bot
   ```

## Tüm sistemi çalıştırma (bot + zamanlayıcı + feed kontrolleri)

```powershell
.\.venv\Scripts\python.exe -m sepetfy run
```

Durdurmak: **Ctrl+C**.

## Güncellemeleri almak

Ben yeni kod gönderdiğimde:

```powershell
cd $HOME\Documents\sepetfy
git pull
.\.venv\Scripts\python.exe -m pip install -e .
```

Veritabanı şeması açılışta otomatik güncellenir, verileriniz korunur.

## Sık karşılaşılan sorunlar

| Belirti | Çözüm |
|---|---|
| `py` veya `git` tanınmıyor | PowerShell'i kapatıp açın; olmazsa kurulumu tekrarlayın |
| Bot cevap vermiyor | `bot` komutu çalışıyor mu? Bilgisayar uykuda mı? Aynı token'la başka yerde çalışan kopya var mı? |
| `Token geçersiz (401)` | `.env` içindeki token eksik/yanlış kopyalanmış; @BotFather → `/mybots` → API Token |
| `Aynı bot başka bir yerde de çalışıyor (409)` | Açık kalan diğer PowerShell penceresindeki botu kapatın |
| `telegram-test` mesaj göndermiyor | Telegram'da botu açıp bir kez `/start` yazın |
| `.env` dosyası görünmüyor | Dosya Gezgini → Görünüm → Göster → "Dosya adı uzantıları" ve "Gizli öğeler" |
| Not Defteri `.env.txt` olarak kaydetti | Dosya Gezgini'nde adını `.env` yapın (uzantılar görünür olmalı) |
