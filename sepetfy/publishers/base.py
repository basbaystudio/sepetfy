"""Tüm yayıncıların temel sınıfı.

``publish`` metodu ``final`` niteliğindedir: alt sınıflar yalnızca ``_send`` metodunu
uygular. Akış:

1. Global şalter (``PUBLISHING_ENABLED``) kontrolü.
2. Onay kapısı (``authorize_publication``).
3. ``Publication`` satırı ``publishing`` durumunda yazılır ve COMMIT edilir.
   UNIQUE(content_id, platform) sayesinde paralel/tekrarlı çağrılardan yalnızca biri geçer.
4. ``DRY_RUN`` açıksa gerçek gönderim yapılmaz, ``dry_run`` olarak işaretlenir.
5. Aksi hâlde ``_send`` çağrılır; sonuç kaydedilir.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sepetfy.app.errors import DuplicatePublication, PublishNotAllowed
from sepetfy.approval.guard import authorize_publication
from sepetfy.config import Settings
from sepetfy.database.models import (
    ContentItem, ContentStatus, Platform, Publication, PublicationStatus, utcnow,
)

log = logging.getLogger(__name__)


@dataclass
class SendResult:
    external_post_id: str | None = None
    external_url: str | None = None


class BasePublisher(ABC):
    platform: Platform

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def _send(self, content: ContentItem) -> SendResult:
        """Platforma gerçek gönderim. Yalnızca ``publish`` tarafından çağrılır."""

    @final
    def publish(self, session: Session, content_id: int) -> Publication:
        if not self.settings.publishing_enabled:
            raise PublishNotAllowed("Yayın şalteri kapalı (PUBLISHING_ENABLED=false)")

        approval = authorize_publication(session, content_id, self.platform, self.settings.admin_user_ids)
        content = session.get(ContentItem, content_id)
        assert content is not None

        pub = Publication(
            content_id=content_id, platform=self.platform,
            approval_id=approval.id, status=PublicationStatus.PUBLISHING,
        )
        session.add(pub)
        try:
            session.commit()  # yayın kilidi: commit edilmeden gönderim yapılmaz
        except IntegrityError as exc:
            session.rollback()
            raise DuplicatePublication(f"İçerik {content_id} {self.platform.value} için zaten kilitli") from exc

        if self.settings.dry_run:
            pub.status = PublicationStatus.DRY_RUN
            session.commit()
            log.info("DRY_RUN: içerik %s %s için gönderilmedi", content_id, self.platform.value)
            return pub

        try:
            result = self._send(content)
        except Exception as exc:
            pub.status = PublicationStatus.FAILED
            pub.error = type(exc).__name__
            session.commit()
            log.exception("Yayın başarısız: içerik %s / %s", content_id, self.platform.value)
            raise

        pub.status = PublicationStatus.PUBLISHED
        pub.external_post_id = result.external_post_id
        pub.external_url = result.external_url
        pub.published_at = utcnow()
        content.status = ContentStatus.PUBLISHED
        session.commit()
        log.info("Yayınlandı: içerik %s / %s", content_id, self.platform.value)
        return pub
