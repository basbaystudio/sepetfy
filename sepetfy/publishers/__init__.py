"""Platform yayıncıları. Hepsi ``BasePublisher``'dan türer ve onay kapısından geçer.

Platform yayıncıları FAZ 6'da sırayla eklenir: Telegram -> Instagram -> TikTok -> Pinterest -> diğerleri.
Resmî yayın API'si olmayan platformlar (ör. WhatsApp Kanalı) için otomatik yayın YAPILMAZ;
onaylanan içerik kopyala-yapıştır paketi olarak size iletilir.
"""
