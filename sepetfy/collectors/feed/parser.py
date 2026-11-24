"""XML feed ayrıştırıcı.

- Akış (streaming) ile okur: yüz binlerce ürünlük feed'ler küçük sunucuda belleği doldurmaz.
- ``defusedxml`` kullanır: üçüncü taraf XML'deki XXE / "billion laughs" saldırılarına karşı korur.
- Türkçe ve İngilizce fiyat biçimlerini doğru sayıya çevirir ("1.299,90 TL" -> 1299.90).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from defusedxml.ElementTree import iterparse

from sepetfy.collectors.feed.config import FeedConfig

_CENT = Decimal("0.01")


# ---------------------------------------------------------------- değer dönüştürücüler
def parse_price(text: str | None, decimal: str = "auto") -> Decimal | None:
    if text is None:
        return None
    s = re.sub(r"[^\d,.\-]", "", str(text))
    if not re.search(r"\d", s):
        return None
    if decimal == "comma":
        s = s.replace(".", "").replace(",", ".")
    elif decimal == "dot":
        s = s.replace(",", "")
    elif "," in s and "." in s:
        # en sağdaki ayraç ondalık ayracıdır
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"[1-9]\d{0,2}(,\d{3}){2,}", s) else s.replace(",", ".")
    elif "." in s:
        # "1.299" / "12.500" Türkçe binlik ayracı; "12.5" / "12.50" ondalık
        if re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", s):
            s = s.replace(".", "")
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return value.quantize(_CENT)


_FALSE = ("out of stock", "out_of_stock", "outofstock", "stokta yok", "tükendi", "tukendi",
          "yok", "false", "no", "hayır", "hayir", "0", "discontinued", "unavailable")
_TRUE = ("in stock", "in_stock", "instock", "stokta", "var", "true", "yes", "evet", "1",
         "available", "preorder", "limited")


def parse_bool(text: str | None) -> bool | None:
    if text is None:
        return None
    t = str(text).strip().lower()
    if not t:
        return None
    if t in _FALSE or any(t.startswith(f) for f in ("out of stock", "stokta yok")):
        return False
    if t in _TRUE:
        return True
    return None


def parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    m = re.search(r"-?\d+", str(text).replace(".", "").replace(",", ""))
    return int(m.group()) if m else None


def parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(text))
    return float(m.group().replace(",", ".")) if m else None


# ---------------------------------------------------------------- XML okuma
def _local(tag: str) -> str:
    if "}" in tag:
        tag = tag.rsplit("}", 1)[1]
    return tag.split(":")[-1]


def _flatten(elem, prefix: str = "", out: dict | None = None) -> dict[str, str]:
    out = {} if out is None else out
    for k, v in elem.attrib.items():
        out.setdefault(f"{prefix}@{_local(k)}", v.strip())
    for child in elem:
        path = f"{prefix}{_local(child.tag)}"
        text = (child.text or "").strip()
        if text:
            out.setdefault(path, text)
        _flatten(child, path + "/", out)
    return out


def iter_raw_items(path: Path, item_tag: str) -> Iterator[dict[str, str]]:
    """Her ``item_tag`` öğesini düz bir {yol: metin} sözlüğü olarak verir.

    İşlenen öğe ağaçtan tamamen çıkarılır; bellek kullanımı feed boyutundan bağımsızdır.
    """
    stack = []
    for event, elem in iterparse(str(path), events=("start", "end")):
        if event == "start":
            stack.append(elem)
            continue
        stack.pop()
        if _local(elem.tag) == item_tag:
            yield _flatten(elem)
            elem.clear()
            if stack:
                stack[-1].remove(elem)


# ---------------------------------------------------------------- eşleştirme
@dataclass
class FeedItem:
    external_id: str
    title: str
    url: str
    price: Decimal
    brand: str | None = None
    category: str | None = None
    affiliate_url: str | None = None
    image_url: str | None = None
    list_price: Decimal | None = None
    in_stock: bool | None = None
    shipping_cost: Decimal | None = None
    coupon_code: str | None = None
    seller_name: str | None = None
    seller_rating: float | None = None
    rating: float | None = None
    review_count: int | None = None
    currency: str = "TRY"


def _get(raw: dict[str, str], spec: str | list[str] | None) -> str | None:
    if spec is None:
        return None
    for key in [spec] if isinstance(spec, str) else spec:
        key = "/".join(_local(p) if not p.startswith("@") else "@" + _local(p[1:]) for p in key.split("/"))
        val = raw.get(key)
        if val:
            return val
    return None


def _clip(text: str | None, n: int) -> str | None:
    return text[:n] if text else text


def map_item(raw: dict[str, str], cfg: FeedConfig) -> FeedItem | None:
    """Ham öğeyi ``FeedItem``'a çevirir. Zorunlu alanlar eksik/geçersizse ``None``."""
    f = cfg.fields
    ext_id = _get(raw, f["external_id"])
    title = _get(raw, f["title"])
    url = _get(raw, f["url"])
    price = parse_price(_get(raw, f["price"]), cfg.decimal)
    if not (ext_id and title and url and price is not None and price > 0):
        return None

    currency = (_get(raw, f.get("currency")) or "").upper()
    if not currency:
        m = re.search(r"\b([A-Z]{3})\b", _get(raw, f["price"]) or "")
        currency = m.group(1) if m else cfg.currency

    in_stock = parse_bool(_get(raw, f.get("in_stock")))
    if in_stock is None and "stock_qty" in f:
        qty = parse_int(_get(raw, f["stock_qty"]))
        in_stock = None if qty is None else qty > 0

    list_price = parse_price(_get(raw, f.get("list_price")), cfg.decimal)
    if list_price is not None and list_price <= 0:
        list_price = None

    return FeedItem(
        external_id=_clip(ext_id, 128),
        title=_clip(title, 512),
        url=url,
        price=price,
        brand=_clip(_get(raw, f.get("brand")), 128),
        category=_clip(_get(raw, f.get("category")), 128),
        affiliate_url=_get(raw, f.get("affiliate_url")),
        image_url=_get(raw, f.get("image_url")),
        list_price=list_price,
        in_stock=in_stock,
        shipping_cost=parse_price(_get(raw, f.get("shipping_cost")), cfg.decimal),
        coupon_code=_clip(_get(raw, f.get("coupon_code")), 64),
        seller_name=_clip(_get(raw, f.get("seller_name")), 256),
        seller_rating=parse_float(_get(raw, f.get("seller_rating"))),
        rating=parse_float(_get(raw, f.get("rating"))),
        review_count=parse_int(_get(raw, f.get("review_count"))),
        currency=currency,
    )


@dataclass
class ParseResult:
    items: list[FeedItem]
    raw_count: int
    invalid_count: int
    duplicate_count: int

    @property
    def valid_count(self) -> int:
        return self.raw_count - self.invalid_count - self.duplicate_count


def iter_feed_items(path: Path, cfg: FeedConfig, counts: ParseResult | None = None) -> Iterator[FeedItem]:
    """Geçerli ve tekrarsız ürünleri akış hâlinde verir; isteğe bağlı olarak sayaçları doldurur."""
    seen: set[str] = set()
    for raw in iter_raw_items(path, cfg.item_tag):
        if counts is not None:
            counts.raw_count += 1
        item = map_item(raw, cfg)
        if item is None:
            if counts is not None:
                counts.invalid_count += 1
            continue
        if item.external_id in seen:
            if counts is not None:
                counts.duplicate_count += 1
            continue
        seen.add(item.external_id)
        yield item


def scan_feed(path: Path, cfg: FeedConfig) -> ParseResult:
    """Yalnızca sayar (ürünleri bellekte tutmaz): sağlamlık kontrolü için ilk geçiş."""
    counts = ParseResult([], 0, 0, 0)
    for _ in iter_feed_items(path, cfg, counts):
        pass
    return counts


def parse_feed(path: Path, cfg: FeedConfig) -> ParseResult:
    """Tüm ürünleri listeye toplar (küçük feed'ler ve testler için)."""
    res = ParseResult([], 0, 0, 0)
    res.items = list(iter_feed_items(path, cfg, res))
    return res
