"""Feed hattının uçtan uca testleri (zamanda ilerleyen senaryolar)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from sepetfy.collectors.feed import FeedConfig, import_feed_file, run_feed, stale_feeds
from sepetfy.database import session_scope
from sepetfy.database.models import Deal, FeedState, PriceSnapshot, Product
from sepetfy.database.seed import seed_stores

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
ENV = "TEST_FEED_URL"


def feed_xml(products: dict[str, tuple[str, str | None, str]]) -> bytes:
    """products: id -> (fiyat, liste_fiyatı, stok)"""
    items = "".join(
        f"<item><id>{pid}</id><title>Ürün {pid}</title><link>https://e.com/{pid}</link>"
        f"<price>{price}</price>{f'<old>{old}</old>' if old else ''}<stock>{stock}</stock></item>"
        for pid, (price, old, stock) in products.items()
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><feed>{items}</feed>'.encode()


@pytest.fixture
def cfg(settings, monkeypatch):
    monkeypatch.setenv(ENV, "https://feeds.example.com/f.xml?token=abc")
    with session_scope() as s:
        seed_stores(s)
    return FeedConfig.from_dict({
        "name": "test", "store_code": "hepsiburada", "url_env": ENV, "item_tag": "item",
        "fields": {"external_id": "id", "title": "title", "url": "link", "price": "price",
                   "list_price": "old", "in_stock": "stock"},
    })


class FakeServer:
    def __init__(self):
        self.body = b""
        self.calls = 0

    def client(self):
        def handler(req):
            self.calls += 1
            return httpx.Response(200, content=self.body)
        return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def server():
    return FakeServer()


def run(cfg, settings, server, products, day: float):
    server.body = feed_xml(products) if isinstance(products, dict) else products
    with session_scope() as s, server.client() as c:
        return run_feed(s, cfg, settings.data_dir, client=c, now=T0 + timedelta(days=day))


def count(model):
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_first_run_adds_products_and_history(cfg, settings, server):
    r = run(cfg, settings, server, {"A": ("100", None, "1"), "B": ("200", None, "1")}, 0)
    assert r.status == "synced" and r.stats["new"] == 2
    assert count(Product) == 2 and count(PriceSnapshot) == 2
    assert (settings.data_dir / "feeds" / "test.last_good.xml").exists()


def test_unchanged_feed_is_skipped(cfg, settings, server):
    p = {"A": ("100", None, "1")}
    run(cfg, settings, server, p, 0)
    r = run(cfg, settings, server, p, 0.1)
    assert r.status == "unchanged"
    assert count(PriceSnapshot) == 1


def test_only_changes_create_snapshots(cfg, settings, server):
    run(cfg, settings, server, {"A": ("100", None, "1"), "B": ("200", None, "1")}, 0)
    r = run(cfg, settings, server, {"A": ("100", None, "1"), "B": ("210", None, "1")}, 1)
    assert r.stats["unchanged"] == 1 and r.stats["changed"] == 1
    assert count(PriceSnapshot) == 3


def test_verified_drop_creates_deal(cfg, settings, server):
    run(cfg, settings, server, {"A": ("1.000,00", None, "1")}, 0)
    r = run(cfg, settings, server, {"A": ("750,00", "1.000,00", "1")}, 10)
    assert r.stats["price_drops"] == 1 and len(r.deal_ids) == 1
    with session_scope() as s:
        d = s.get(Deal, r.deal_ids[0])
        assert d.discount_verified and d.verified_discount_pct == 25.0
        assert d.reference_price == Decimal("1000.00")


def test_drop_without_enough_history_is_not_a_deal(cfg, settings, server):
    run(cfg, settings, server, {"A": ("1000", None, "1")}, 0)
    r = run(cfg, settings, server, {"A": ("500", "2000", "1")}, 2)  # 2 gün geçmiş < 7 gün
    assert r.stats["price_drops"] == 1 and r.deal_ids == []


def test_inflate_then_discount_trick_is_caught(cfg, settings, server):
    """Fiyatı önce yükseltip sonra 'indirim' yapmak: 30 günün en düşüğüne göre indirim yok."""
    run(cfg, settings, server, {"A": ("100", None, "1")}, 0)
    run(cfg, settings, server, {"A": ("150", None, "1")}, 10)    # şişirme
    r = run(cfg, settings, server, {"A": ("120", "150", "1")}, 20)  # "%20 indirim!"
    assert r.stats["price_drops"] == 1 and r.deal_ids == []


def test_inflated_list_price_flagged(cfg, settings, server):
    run(cfg, settings, server, {"A": ("1000", None, "1")}, 0)
    r = run(cfg, settings, server, {"A": ("800", "2500", "1")}, 10)  # mağaza 'eski fiyat 2500' diyor
    with session_scope() as s:
        d = s.get(Deal, r.deal_ids[0])
        assert d.score_breakdown["inflated_list_price"] is True
        assert d.verified_discount_pct == 20.0  # bizim hesabımız %20, mağazanın iddiası %68
        assert any("Şüpheli eski fiyat" in x for x in d.reasons)


def test_out_of_stock_drop_is_not_a_deal(cfg, settings, server):
    run(cfg, settings, server, {"A": ("1000", None, "1")}, 0)
    r = run(cfg, settings, server, {"A": ("500", None, "0")}, 10)
    assert r.deal_ids == []


def test_same_deal_not_created_twice(cfg, settings, server):
    run(cfg, settings, server, {"A": ("1000", None, "1")}, 0)
    run(cfg, settings, server, {"A": ("700", None, "1")}, 10)
    run(cfg, settings, server, {"A": ("700", None, "0")}, 10.5)  # stok değişti -> yeni snapshot, düşüş değil
    assert count(Deal) == 1


def test_missing_products_deactivated_after_three_runs(cfg, settings, server):
    base = {f"P{i}": ("10", None, "1") for i in range(10)}
    run(cfg, settings, server, base, 0)
    for day in (1, 2, 3):
        smaller = {k: (str(10 + day), None, "1") for k in list(base)[:9]}  # P9 yok
        run(cfg, settings, server, smaller, day)
    with session_scope() as s:
        p9 = s.scalar(select(Product).where(Product.external_id == "P9"))
        p0 = s.scalar(select(Product).where(Product.external_id == "P0"))
        assert p9.active is False and p9.missing_count == 3
        assert p0.active is True


def test_truncated_feed_rejected_and_db_untouched(cfg, settings, server):
    run(cfg, settings, server, {f"P{i}": ("10", None, "1") for i in range(100)}, 0)
    r = run(cfg, settings, server, {f"P{i}": ("5", None, "1") for i in range(10)}, 1)
    assert r.status == "rejected" and "ani düştü" in r.message
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Product).where(Product.active.is_(False))) == 0
        assert s.scalar(select(func.count()).select_from(PriceSnapshot)) == 100
        st = s.get(FeedState, "test")
        assert st.consecutive_failures == 1 and "REDDEDİLDİ" in st.last_error
        assert st.last_success_at is not None  # son sağlam çalıştırma korunur


def test_malformed_xml_rejected(cfg, settings, server):
    run(cfg, settings, server, {"A": ("10", None, "1")}, 0)
    r = run(cfg, settings, server, b"<feed><item><id>A</id>", 1)
    assert r.status == "rejected" and "XML" in r.message


def test_broken_mapping_rejected(cfg, settings, server):
    r = run(cfg, settings, server, b"<feed><item><kod>1</kod></item><item><kod>2</kod></item></feed>", 0)
    assert r.status == "rejected"


def test_missing_url_reports_failure(cfg, settings, server, monkeypatch):
    monkeypatch.delenv(ENV)
    with session_scope() as s:
        r = run_feed(s, cfg, settings.data_dir, client=server.client(), now=T0)
    assert r.status == "failed" and ENV in r.message and server.calls == 0


def test_server_error_recorded(cfg, settings):
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(404)))
    with session_scope() as s:
        r = run_feed(s, cfg, settings.data_dir, client=client, now=T0)
    assert r.status == "failed" and "404" in r.message and "token" not in r.message


def test_manual_file_import(cfg, settings, tmp_path):
    f = tmp_path / "elle.xml"
    f.write_bytes(feed_xml({"A": ("99,90", None, "1")}))
    with session_scope() as s:
        r = import_feed_file(s, cfg, f, now=T0)
    assert r.status == "synced" and r.stats["new"] == 1


def test_stale_feed_detection(cfg, settings, server):
    configs = {"test": cfg}
    with session_scope() as s:
        assert stale_feeds(s, configs, now=T0) == ["test"]  # hiç başarı yok
    run(cfg, settings, server, {"A": ("10", None, "1")}, 0)
    with session_scope() as s:
        assert stale_feeds(s, configs, now=T0 + timedelta(hours=2)) == []
        assert stale_feeds(s, configs, now=T0 + timedelta(hours=30)) == ["test"]


def test_failure_after_writes_rolls_back_everything(cfg, settings, server, monkeypatch):
    """Ürünler yazıldıktan SONRA hata çıkarsa da hiçbir şey kalıcı olmamalı (SAVEPOINT)."""
    from sepetfy.collectors.feed import runner

    real_sync = runner.sync_items

    def sync_then_fail(*a, **kw):
        real_sync(*a, **kw)
        raise runner.FeedRejected("yazımdan sonra yapay hata")

    monkeypatch.setattr(runner, "sync_items", sync_then_fail)
    r = run(cfg, settings, server, {"A": ("10", None, "1"), "B": ("20", None, "1")}, 0)
    assert r.status == "rejected"
    assert count(Product) == 0 and count(PriceSnapshot) == 0
    with session_scope() as s:
        assert s.get(FeedState, "test").consecutive_failures == 1  # hata kaydı ise korunur
