"""ONAY KAPISI — sistemdeki tek yayın izni kaynağı.

Hiçbir yayıncı bu fonksiyonu atlayarak yayın yapamaz: ``publishers.base.BasePublisher.publish``
her yayından önce ``authorize_publication`` çağırır ve ``Publication`` satırı
``approval_id`` olmadan oluşturulamaz (NOT NULL), ``(content_id, platform)`` UNIQUE'dir.

Yayın izni için şartların HEPSİ sağlanmalı:
1. İçeriğin EN SON onay kaydı ``approve`` kararı olmalı (sonradan red/düzenle gelirse iptal).
2. Onay, yetkili Telegram kullanıcısı tarafından verilmiş olmalı.
3. Hedef platform, onayda seçilen platformlar arasında olmalı.
4. İçerik, onaydan sonra değişmemiş olmalı (``content_hash`` eşleşmeli).
5. İçerik durumu ``approved`` veya ``scheduled`` olmalı.
6. Bu içerik bu platformda daha önce yayınlanmamış / yayınlanıyor olmamalı.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from sepetfy.app.errors import DuplicatePublication, PublishNotAllowed
from sepetfy.database.models import (
    ApprovalDecision, ApprovalRecord, ContentItem, ContentStatus, Platform, Publication,
)


def compute_content_hash(body: str, media_paths: list | None, platform: str) -> str:
    payload = json.dumps({"b": body, "m": sorted(media_paths or []), "p": platform},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def current_hash(content: ContentItem) -> str:
    return compute_content_hash(content.body, content.media_paths, Platform(content.platform).value)


def latest_approval(session: Session, content_id: int) -> ApprovalRecord | None:
    return session.scalar(
        select(ApprovalRecord)
        .where(ApprovalRecord.content_id == content_id)
        .order_by(ApprovalRecord.id.desc())
        .limit(1)
    )


def authorize_publication(
    session: Session, content_id: int, platform: Platform, admin_ids: frozenset[int]
) -> ApprovalRecord:
    """Yayına izin veren onay kaydını döndürür; aksi hâlde ``PublishNotAllowed`` fırlatır."""
    platform = Platform(platform)
    if not admin_ids:
        raise PublishNotAllowed("Yetkili onaylayıcı tanımlı değil (TELEGRAM_ADMIN_USER_IDS)")

    content = session.get(ContentItem, content_id)
    if content is None:
        raise PublishNotAllowed(f"İçerik bulunamadı: {content_id}")

    approval = latest_approval(session, content_id)
    if approval is None:
        raise PublishNotAllowed(f"İçerik {content_id} için onay kaydı yok")
    if approval.decision != ApprovalDecision.APPROVE:
        raise PublishNotAllowed(f"İçerik {content_id} son karar: {approval.decision.value}")
    if approval.actor_telegram_id not in admin_ids:
        raise PublishNotAllowed("Onay yetkisiz bir kullanıcıdan geliyor")
    if platform.value not in (approval.platforms or []):
        raise PublishNotAllowed(f"{platform.value} platformu onaylanmadı")
    if approval.content_hash != current_hash(content):
        raise PublishNotAllowed("İçerik onaydan sonra değişti; yeniden onay gerekli")
    if content.status not in (ContentStatus.APPROVED, ContentStatus.SCHEDULED):
        raise PublishNotAllowed(f"İçerik durumu uygun değil: {content.status.value}")

    existing = session.scalar(
        select(Publication).where(Publication.content_id == content_id, Publication.platform == platform)
    )
    if existing is not None:
        raise DuplicatePublication(
            f"İçerik {content_id} {platform.value} için zaten kayıtlı (durum: {existing.status.value})"
        )
    return approval


def record_decision(
    session: Session,
    content: ContentItem,
    decision: ApprovalDecision,
    actor_telegram_id: int,
    admin_ids: frozenset[int],
    platforms: list[str] | None = None,
    note: str | None = None,
) -> ApprovalRecord:
    """Onay kararını kaydeder ve içerik durumunu günceller. Yetkisiz kullanıcıyı reddeder."""
    if actor_telegram_id not in admin_ids:
        raise PublishNotAllowed("Yetkisiz kullanıcı karar veremez")
    platforms = [Platform(p).value for p in (platforms or [])]
    if decision == ApprovalDecision.APPROVE and not platforms:
        raise PublishNotAllowed("Onay için en az bir platform seçilmeli")

    rec = ApprovalRecord(
        content_id=content.id,
        decision=decision,
        platforms=platforms if decision == ApprovalDecision.APPROVE else [],
        content_hash=current_hash(content),
        actor_telegram_id=actor_telegram_id,
        note=note,
    )
    session.add(rec)
    content.status = {
        ApprovalDecision.APPROVE: ContentStatus.APPROVED,
        ApprovalDecision.REJECT: ContentStatus.REJECTED,
        ApprovalDecision.EDIT: ContentStatus.NEEDS_EDIT,
        ApprovalDecision.LATER: ContentStatus.PENDING_APPROVAL,
    }[decision]
    session.flush()
    return rec
