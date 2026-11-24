from __future__ import annotations

from decimal import Decimal

import pytest

from sepetfy.config import Settings
from sepetfy.database import create_schema, init_engine, session_scope
from sepetfy.database.models import (
    ContentItem, ContentStatus, Deal, Platform, PriceSnapshot, Product, Store,
)

ADMIN_ID = 111222333


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # Gerçek .env dosyasının testleri etkilememesi için
    monkeypatch.chdir(tmp_path)
    s = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        log_dir=tmp_path / "logs",
        backup_dir=tmp_path / "backups",
        telegram_admin_user_ids=str(ADMIN_ID),
        publishing_enabled=True,
        dry_run=True,
        monthly_budget_usd=10,
    )
    init_engine(s.database_url)
    create_schema()
    return s


@pytest.fixture
def content_id(settings):
    from sepetfy.approval.guard import compute_content_hash

    with session_scope() as s:
        store = Store(code="t", name="T", access_method="manual", access_status="verified")
        prod = Product(store=store, external_id="p1", title="Ürün", url="https://example.com/p1")
        snap = PriceSnapshot(product=prod, price=Decimal("100"), source="test")
        s.add_all([store, prod, snap])
        s.flush()
        deal = Deal(product_id=prod.id, snapshot_id=snap.id, fingerprint="fp1")
        s.add(deal)
        s.flush()
        body = "Süper fırsat!"
        c = ContentItem(deal_id=deal.id, platform=Platform.TELEGRAM, kind="message", body=body,
                        content_hash=compute_content_hash(body, None, "telegram"),
                        status=ContentStatus.PENDING_APPROVAL)
        s.add(c)
        s.flush()
        return c.id
