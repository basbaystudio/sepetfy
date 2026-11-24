"""Veritabanı şeması (SQLAlchemy 2.0). SQLite ve PostgreSQL ile uyumludur.

Önemli bütünlük kuralları şema seviyesinde zorunludur:
- ``publications(content_id, platform)`` UNIQUE  -> aynı içerik bir platformda iki kez yayınlanamaz.
- ``publications.approval_id`` NOT NULL         -> onay kaydı olmadan yayın satırı oluşamaz.
- ``approval_records`` yalnızca eklenir (append-only); güncellenmez/silinmez.
- ``price_snapshots`` fiyat geçmişidir; mağazanın gösterdiği eski fiyat ``list_price``
  olarak ayrı tutulur ve doğrulanmış referans fiyat yerine geçmez.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger, JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite saat dilimi bilgisini saklamaz; okunan naive değerleri UTC kabul et."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- enumlar
class DealGrade(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    REJECT = "REJECT"  # yanıltıcı / doğrulanamayan -> yayın kuyruğuna girmez


class DealStatus(str, enum.Enum):
    NEW = "new"
    CONTENT_READY = "content_ready"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    EXPIRED = "expired"


class ContentStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_EDIT = "needs_edit"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class ApprovalDecision(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    LATER = "later"


class PublicationStatus(str, enum.Enum):
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    DRY_RUN = "dry_run"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


class Platform(str, enum.Enum):
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    WHATSAPP = "whatsapp"
    PINTEREST = "pinterest"
    X = "x"


def _enum(e: type[enum.Enum]) -> Enum:
    # Değerleri (ör. "approve") sakla; native enum kullanma (SQLite/Postgres taşınabilirliği)
    return Enum(e, native_enum=False, values_callable=lambda x: [m.value for m in x], length=32)


# ---------------------------------------------------------------- mağaza & ürün
class Store(Base, TimestampMixin):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512), default="")
    access_method: Mapped[str] = mapped_column(String(32))
    access_status: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    check_interval_min: Mapped[int] = mapped_column(Integer, default=15)
    notes: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    products: Mapped[list[Product]] = relationship(back_populates="store")


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("store_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(512))
    brand: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    affiliate_url: Mapped[str | None] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    seller_name: Mapped[str | None] = mapped_column(String(256))
    seller_rating: Mapped[float | None]
    rating: Mapped[float | None]
    review_count: Mapped[int | None]
    # Feed'den kaybolan ürün hemen "stokta yok" sayılmaz; art arda N kontrolde yoksa pasife alınır.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    store: Mapped[Store] = relationship(back_populates="products")
    snapshots: Mapped[list[PriceSnapshot]] = relationship(back_populates="product", order_by="PriceSnapshot.observed_at")


class PriceSnapshot(Base):
    """Her kontrolde gözlenen fiyat. Fiyat geçmişinin kaynağıdır."""

    __tablename__ = "price_snapshots"
    __table_args__ = (Index("ix_snap_product_time", "product_id", "observed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    list_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # mağazanın gösterdiği "eski" fiyat
    currency: Mapped[str] = mapped_column(String(3), default="TRY")
    shipping_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    coupon_code: Mapped[str | None] = mapped_column(String(64))
    coupon_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    in_stock: Mapped[bool | None]
    source: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    product: Mapped[Product] = relationship(back_populates="snapshots")


class FeedState(Base):
    """Bir ürün feed'inin son durumu: değişiklik tespiti ve izleme için."""

    __tablename__ = "feed_states"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    etag: Mapped[str | None] = mapped_column(String(256))
    last_modified: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_item_count: Mapped[int | None]
    last_stats: Mapped[dict | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- fırsat
class Deal(Base, TimestampMixin):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("price_snapshots.id"))
    # Aynı ürün+fiyat+kupon için tekrar analiz yapılmasını engeller
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    score: Mapped[int | None]
    grade: Mapped[DealGrade | None] = mapped_column(_enum(DealGrade))
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # kendi kayıtlarımızdan
    verified_discount_pct: Mapped[float | None]
    discount_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON)
    reasons: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[DealStatus] = mapped_column(_enum(DealStatus), default=DealStatus.NEW, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship()
    contents: Mapped[list[ContentItem]] = relationship(back_populates="deal")


# ---------------------------------------------------------------- içerik & onay
class ContentItem(Base, TimestampMixin):
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"))
    platform: Mapped[Platform] = mapped_column(_enum(Platform))
    kind: Mapped[str] = mapped_column(String(32))  # post, story, reel_script, pin, message...
    body: Mapped[str] = mapped_column(Text)
    media_paths: Mapped[list | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("content_items.id"))  # düzenleme zinciri
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[ContentStatus] = mapped_column(_enum(ContentStatus), default=ContentStatus.DRAFT, index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    deal: Mapped[Deal] = relationship(back_populates="contents")
    approvals: Mapped[list[ApprovalRecord]] = relationship(back_populates="content", order_by="ApprovalRecord.id")


class ApprovalRecord(Base):
    """Onay kararı. Yalnızca eklenir; güncelleme ve silme engellidir."""

    __tablename__ = "approval_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"), index=True)
    decision: Mapped[ApprovalDecision] = mapped_column(_enum(ApprovalDecision))
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64))  # onaylanan içeriğin özeti
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    content: Mapped[ContentItem] = relationship(back_populates="approvals")


@event.listens_for(ApprovalRecord, "before_update")
def _approval_immutable_update(mapper, connection, target):  # noqa: ARG001
    raise RuntimeError("approval_records değiştirilemez (append-only)")


@event.listens_for(ApprovalRecord, "before_delete")
def _approval_immutable_delete(mapper, connection, target):  # noqa: ARG001
    raise RuntimeError("approval_records silinemez (append-only)")


class Publication(Base, TimestampMixin):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("content_id", "platform", name="uq_publication_content_platform"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"))
    platform: Mapped[Platform] = mapped_column(_enum(Platform))
    approval_id: Mapped[int] = mapped_column(ForeignKey("approval_records.id"), nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(_enum(PublicationStatus))
    external_post_id: Mapped[str | None] = mapped_column(String(256))
    external_url: Mapped[str | None] = mapped_column(String(1024))
    error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- maliyet, performans, önbellek
class CostEntry(Base, TimestampMixin):
    __tablename__ = "cost_entries"
    __table_args__ = (Index("ix_cost_service_time", "service", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(32))  # claude, gemini, kling, server, storage, other
    operation: Mapped[str] = mapped_column(String(64))
    units: Mapped[float] = mapped_column(default=0)
    unit: Mapped[str] = mapped_column(String(32), default="")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[int | None]
    meta: Mapped[dict | None] = mapped_column(JSON)


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"
    __table_args__ = (Index("ix_metric_pub", "publication_id", "metric"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"))
    metric: Mapped[str] = mapped_column(String(32))  # views, reach, likes, clicks, sales, commission...
    value: Mapped[float]
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AICache(Base, TimestampMixin):
    """Aynı girdiyle tekrar AI çağrısı yapılmasını önleyen önbellek."""

    __tablename__ = "ai_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32))
    response: Mapped[dict] = mapped_column(JSON)


class AppSetting(Base):
    """Çalışma zamanında Telegram'dan değiştirilebilecek ayarlar (ör. aylık bütçe)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
