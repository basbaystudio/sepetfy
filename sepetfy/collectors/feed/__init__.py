"""Genel XML ürün feed'i toplayıcısı (affiliate ağları, mağaza feed'leri, Google Shopping biçimi).

Yeni bir feed eklemek = ``sepetfy/config/feeds/<ad>.json`` alan eşleştirme dosyası + .env'de URL.
"""

from sepetfy.collectors.feed.config import FeedConfig, load_feed_configs
from sepetfy.collectors.feed.runner import RunReport, import_feed_file, run_feed, stale_feeds

__all__ = ["FeedConfig", "RunReport", "import_feed_file", "load_feed_configs", "run_feed", "stale_feeds"]
