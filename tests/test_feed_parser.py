from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from defusedxml import DefusedXmlException

from sepetfy.app.errors import ConfigError
from sepetfy.collectors.feed.config import FEEDS_DIR, FeedConfig, load_feed_configs
from sepetfy.collectors.feed.parser import parse_bool, parse_feed, parse_price

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("text,expected", [
    ("1.299,90 TL", "1299.90"),
    ("1299,90", "1299.90"),
    ("1299.90 TRY", "1299.90"),
    ("1,299.90", "1299.90"),
    ("₺12.500", "12500.00"),
    ("1.299", "1299.00"),
    ("12.5", "12.50"),
    ("0.999", "1.00"),
    ("349", "349.00"),
    ("1.234.567,8", "1234567.80"),
    ("1,234,567", "1234567.00"),
    ("0 TRY", "0.00"),
])
def test_parse_price_auto(text, expected):
    assert parse_price(text) == Decimal(expected)


@pytest.mark.parametrize("text", [None, "", "TL", "ücretsiz", "-5"])
def test_parse_price_invalid(text):
    assert parse_price(text) is None


def test_parse_price_forced_modes():
    assert parse_price("1.299", "dot") == Decimal("1.30")
    assert parse_price("12,5", "comma") == Decimal("12.50")


@pytest.mark.parametrize("text,expected", [
    ("in stock", True), ("Stokta", True), ("1", True), ("var", True),
    ("out of stock", False), ("Stokta yok", False), ("0", False), ("Tükendi", False),
    ("bilinmiyor", None), (None, None),
])
def test_parse_bool(text, expected):
    assert parse_bool(text) is expected


def _google_cfg(**kw) -> FeedConfig:
    data = {**{k: v for k, v in __import__("json").loads((FEEDS_DIR / "hepsiburada.json").read_text()).items()}, **kw}
    return FeedConfig.from_dict(data)


def test_google_shopping_mapping():
    res = parse_feed(FIXTURES / "google_shopping.xml", _google_cfg())
    assert (res.raw_count, len(res.items), res.invalid_count, res.duplicate_count) == (4, 2, 1, 1)
    a, b = res.items
    assert a.external_id == "HB-001"
    assert a.price == Decimal("1199.90") and a.list_price == Decimal("1499.90")
    assert a.in_stock is True and a.shipping_cost == Decimal("0.00")
    assert a.category == "Elektronik > Kulaklık" and a.currency == "TRY"
    assert b.price == Decimal("349.00") and b.in_stock is False


def test_generic_turkish_mapping(tmp_path):
    xml = tmp_path / "f.xml"
    xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<urunler><urun kod="A1"><ad>Çanta</ad><link>https://e.com/a1</link>
<fiyat><satis>899,00</satis><liste>1.299,00</liste></fiyat><stok>12</stok></urun></urunler>""", encoding="utf-8")
    cfg = FeedConfig.from_dict({
        "name": "genel", "store_code": "x", "item_tag": "urun",
        "fields": {"external_id": "@kod", "title": "ad", "url": "link", "price": "fiyat/satis",
                   "list_price": "fiyat/liste", "stock_qty": "stok"},
    })
    (item,) = parse_feed(xml, cfg).items
    assert item.external_id == "A1" and item.price == Decimal("899.00")
    assert item.list_price == Decimal("1299.00") and item.in_stock is True


def test_xxe_and_entity_bombs_blocked(tmp_path):
    xml = tmp_path / "evil.xml"
    xml.write_text("""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<r><item><id>1</id><t>&x;</t><u>u</u><p>1</p></item></r>""")
    cfg = FeedConfig.from_dict({"name": "e", "store_code": "x", "item_tag": "item",
                                "fields": {"external_id": "id", "title": "t", "url": "u", "price": "p"}})
    with pytest.raises(DefusedXmlException):
        parse_feed(xml, cfg)


def test_config_validation():
    with pytest.raises(ConfigError, match="zorunlu"):
        FeedConfig.from_dict({"name": "a", "store_code": "x", "item_tag": "i", "fields": {"title": "t"}})
    with pytest.raises(ConfigError, match="bilinmeyen"):
        FeedConfig.from_dict({"name": "a", "store_code": "x", "item_tag": "i",
                              "fields": {"external_id": "a", "title": "t", "url": "u", "price": "p", "fiyat": "x"}})


def test_bundled_configs_load():
    configs = load_feed_configs()
    assert "hepsiburada" in configs
    assert configs["hepsiburada"].enabled is False  # gerçek feed görülmeden açılmaz
