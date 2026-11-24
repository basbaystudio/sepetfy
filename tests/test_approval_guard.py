"""Onay mekanizmasının hiçbir yoldan atlatılamadığını doğrulayan testler."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from sepetfy.app.errors import DuplicatePublication, PublishNotAllowed
from sepetfy.approval.guard import authorize_publication, record_decision
from sepetfy.database import session_scope
from sepetfy.database.models import (
    ApprovalDecision, ApprovalRecord, ContentItem, Platform, Publication, PublicationStatus,
)
from sepetfy.publishers.base import BasePublisher, SendResult
from tests.conftest import ADMIN_ID

ADMINS = frozenset({ADMIN_ID})


class FakeTelegramPublisher(BasePublisher):
    platform = Platform.TELEGRAM

    def __init__(self, settings):
        super().__init__(settings)
        self.sent = []

    def _send(self, content):
        self.sent.append(content.id)
        return SendResult(external_post_id="msg-1")


def approve(content_id, platforms=("telegram",), actor=ADMIN_ID):
    with session_scope() as s:
        c = s.get(ContentItem, content_id)
        record_decision(s, c, ApprovalDecision.APPROVE, actor, ADMINS, list(platforms))


def test_no_approval_blocks(settings, content_id):
    with session_scope() as s, pytest.raises(PublishNotAllowed, match="onay kaydı yok"):
        authorize_publication(s, content_id, Platform.TELEGRAM, ADMINS)


def test_unauthorized_user_cannot_decide(settings, content_id):
    with pytest.raises(PublishNotAllowed):
        approve(content_id, actor=999)


def test_approval_requires_platform(settings, content_id):
    with pytest.raises(PublishNotAllowed):
        approve(content_id, platforms=())


def test_platform_not_selected_blocks(settings, content_id):
    approve(content_id, platforms=("telegram",))
    with session_scope() as s, pytest.raises(PublishNotAllowed, match="instagram"):
        authorize_publication(s, content_id, Platform.INSTAGRAM, ADMINS)


def test_later_reject_overrides_approval(settings, content_id):
    approve(content_id)
    with session_scope() as s:
        record_decision(s, s.get(ContentItem, content_id), ApprovalDecision.REJECT, ADMIN_ID, ADMINS)
    with session_scope() as s, pytest.raises(PublishNotAllowed, match="reject"):
        authorize_publication(s, content_id, Platform.TELEGRAM, ADMINS)


def test_edit_after_approval_requires_reapproval(settings, content_id):
    approve(content_id)
    with session_scope() as s:
        s.get(ContentItem, content_id).body = "Değiştirilmiş metin"
    with session_scope() as s, pytest.raises(PublishNotAllowed, match="değişti"):
        authorize_publication(s, content_id, Platform.TELEGRAM, ADMINS)


def test_approved_content_publishes_once(settings, content_id):
    approve(content_id)
    pub = FakeTelegramPublisher(settings)
    with session_scope() as s:
        p = pub.publish(s, content_id)
        assert p.status == PublicationStatus.DRY_RUN  # DRY_RUN açık: gerçek gönderim yok
    assert pub.sent == []
    with session_scope() as s, pytest.raises(DuplicatePublication):
        pub.publish(s, content_id)


def test_real_send_when_dry_run_off(settings, content_id):
    settings.dry_run = False
    approve(content_id)
    pub = FakeTelegramPublisher(settings)
    with session_scope() as s:
        p = pub.publish(s, content_id)
        assert p.status == PublicationStatus.PUBLISHED
    assert pub.sent == [content_id]


def test_kill_switch_blocks_everything(settings, content_id):
    settings.publishing_enabled = False
    approve(content_id)
    with session_scope() as s, pytest.raises(PublishNotAllowed, match="şalter"):
        FakeTelegramPublisher(settings).publish(s, content_id)


def test_db_unique_constraint_prevents_double_publication(settings, content_id):
    approve(content_id)
    with session_scope() as s:
        appr = s.query(ApprovalRecord).one()
        s.add(Publication(content_id=content_id, platform=Platform.TELEGRAM,
                          approval_id=appr.id, status=PublicationStatus.PUBLISHING))
    with pytest.raises(IntegrityError), session_scope() as s:
        appr = s.query(ApprovalRecord).one()
        s.add(Publication(content_id=content_id, platform=Platform.TELEGRAM,
                          approval_id=appr.id, status=PublicationStatus.PUBLISHING))


def test_publication_requires_approval_id(settings, content_id):
    with pytest.raises(IntegrityError), session_scope() as s:
        s.add(Publication(content_id=content_id, platform=Platform.TELEGRAM,
                          approval_id=None, status=PublicationStatus.PUBLISHING))


def test_approval_records_are_append_only_orm(settings, content_id):
    approve(content_id)
    with pytest.raises(RuntimeError), session_scope() as s:
        s.query(ApprovalRecord).one().note = "değiştir"


def test_approval_records_are_append_only_sql(settings, content_id):
    approve(content_id)
    from sqlalchemy import text
    with pytest.raises(DBAPIError), session_scope() as s:
        s.execute(text("DELETE FROM approval_records"))
