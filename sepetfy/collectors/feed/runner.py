"""Bir feed çalıştırmasının uçtan uca yönetimi.

    değişti mi? -> indir -> ayrıştır -> sağlamlık kontrolü -> eşitle -> fırsat adayları -> durum kaydı

Sağlamlık kontrolü başarısız olursa (bozuk XML, ürün sayısında ani çöküş, eşleştirme bozuk)
veritabanına hiçbir şey yazılmaz; son sağlam veri geçerli kalır ve hata kaydedilir.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree.ElementTree import ParseError

import httpx
from defusedxml import DefusedXmlException
from sqlalchemy import select
from sqlalchemy.orm import Session

from sepetfy.app.errors import ConfigError, ExternalServiceError, SepetfyError
from sepetfy.app.resilience import breaker_for
from sepetfy.collectors.feed.config import FeedConfig
from sepetfy.collectors.feed.fetcher import DOWNLOADED, fetch_feed
from sepetfy.collectors.feed.parser import ParseResult, iter_feed_items, scan_feed
from sepetfy.collectors.feed.sync import SyncStats, sync_items
from sepetfy.database.models import FeedState, Store, as_utc, utcnow

log = logging.getLogger(__name__)


class FeedRejected(SepetfyError):
    """Feed sağlamlık kontrolünden geçemedi; veritabanı değiştirilmedi."""


@dataclass
class RunReport:
    feed: str
    status: str  # not_modified | unchanged | synced | rejected | failed
    message: str = ""
    stats: dict = field(default_factory=dict)
    deal_ids: list[int] = field(default_factory=list)


def _get_state(session: Session, cfg: FeedConfig) -> tuple[Store, FeedState]:
    store = session.scalar(select(Store).where(Store.code == cfg.store_code))
    if store is None:
        raise ConfigError(f"Feed '{cfg.name}': mağaza kodu bulunamadı: {cfg.store_code} (önce init-db)")
    state = session.get(FeedState, cfg.name)
    if state is None:
        state = FeedState(name=cfg.name, store_id=store.id, consecutive_failures=0)
        session.add(state)
        session.flush()
    return store, state


def check_sanity(cfg: FeedConfig, parsed: ParseResult, previous_count: int | None) -> None:
    if parsed.raw_count == 0:
        raise FeedRejected(f"'{cfg.item_tag}' etiketinde hiç ürün bulunamadı (eşleştirme veya feed hatalı)")
    if parsed.valid_count == 0:
        raise FeedRejected("Geçerli ürün yok: zorunlu alanlar (id, ad, link, fiyat) okunamadı")
    invalid_ratio = parsed.invalid_count / parsed.raw_count
    if invalid_ratio > cfg.max_invalid_ratio:
        raise FeedRejected(f"Geçersiz ürün oranı çok yüksek: %{invalid_ratio * 100:.0f} (eşleştirme bozuk olabilir)")
    if previous_count and parsed.valid_count < previous_count * cfg.min_item_ratio:
        raise FeedRejected(f"Ürün sayısı ani düştü: {previous_count} -> {parsed.valid_count} (yarım/bozuk feed şüphesi)")


def _process_file(session: Session, cfg: FeedConfig, store: Store, state: FeedState,
                  xml_path: Path, now: datetime) -> RunReport:
    try:
        # 1. geçiş: yalnızca say ve kontrol et (veritabanına dokunmadan)
        parsed = scan_feed(xml_path, cfg)
        check_sanity(cfg, parsed, state.last_item_count)
        # 2. geçiş: akış hâlinde, partiler hâlinde yaz
        stats: SyncStats = sync_items(
            session, store, iter_feed_items(xml_path, cfg), source=f"feed:{cfg.name}", now=now,
            missing_runs_to_deactivate=cfg.missing_runs_to_deactivate,
        )
    except (ParseError, DefusedXmlException) as exc:
        raise FeedRejected(f"XML okunamadı: {type(exc).__name__}") from None
    summary = stats.as_dict() | {"raw": parsed.raw_count, "invalid": parsed.invalid_count,
                                 "duplicates": parsed.duplicate_count}
    state.last_item_count = parsed.valid_count
    state.last_stats = summary
    return RunReport(cfg.name, "synced", stats=summary, deal_ids=stats.deal_ids)


def _mark_failure(session: Session, state: FeedState, message: str, now: datetime) -> None:
    state.last_checked_at = now
    state.last_error = message
    state.consecutive_failures = (state.consecutive_failures or 0) + 1


def run_feed(session: Session, cfg: FeedConfig, data_dir: Path, *,
             client: httpx.Client | None = None, now: datetime | None = None) -> RunReport:
    """Feed'i URL'den kontrol eder ve gerekirse işler. Hataları yakalar, rapor döndürür."""
    now = now or utcnow()
    store, state = _get_state(session, cfg)
    url = cfg.resolve_url()
    if not url:
        msg = f"Feed URL'si tanımlı değil (.env içinde {cfg.url_env})"
        _mark_failure(session, state, msg, now)
        return RunReport(cfg.name, "failed", msg)

    work_dir = data_dir / "feeds"
    own_client = client is None
    client = client or httpx.Client()
    try:
        result = breaker_for(f"feed:{cfg.name}").call(lambda: fetch_feed(
            client, cfg.name, url, work_dir, etag=state.etag, last_modified=state.last_modified,
            previous_hash=state.content_hash, max_bytes=cfg.max_bytes, timeout_sec=cfg.timeout_sec,
        ))
    except ExternalServiceError as exc:
        _mark_failure(session, state, str(exc), now)
        log.error("Feed indirilemedi: %s", exc)
        return RunReport(cfg.name, "failed", str(exc))
    finally:
        if own_client:
            client.close()

    state.last_checked_at = now
    if result.status != DOWNLOADED:
        state.etag, state.last_modified = result.etag or state.etag, result.last_modified or state.last_modified
        state.consecutive_failures = 0
        state.last_error = None
        log.info("Feed %s: %s", cfg.name, "sunucu değişiklik yok dedi" if result.status == "not_modified"
                 else "içerik aynı")
        return RunReport(cfg.name, result.status)

    assert result.path is not None
    report = _process_with_savepoint(session, cfg, store, state, result.path, now)
    if report.status == "synced":
        state.etag, state.last_modified, state.content_hash = result.etag, result.last_modified, result.sha256
        shutil.move(result.path, work_dir / f"{cfg.name}.last_good.xml")
    else:
        result.path.unlink(missing_ok=True)
    return report


def import_feed_file(session: Session, cfg: FeedConfig, xml_path: Path, *, now: datetime | None = None) -> RunReport:
    """Elle indirilmiş / Telegram'dan gönderilmiş XML dosyasını işler (URL'siz feed'ler için)."""
    now = now or utcnow()
    store, state = _get_state(session, cfg)
    state.last_checked_at = now
    return _process_with_savepoint(session, cfg, store, state, xml_path, now)


def _process_with_savepoint(session, cfg, store, state, xml_path, now) -> RunReport:
    try:
        with session.begin_nested():
            report = _process_file(session, cfg, store, state, xml_path, now)
    except FeedRejected as exc:
        _mark_failure(session, state, f"REDDEDİLDİ: {exc}", now)
        log.error("Feed %s reddedildi, veritabanı değiştirilmedi: %s", cfg.name, exc)
        return RunReport(cfg.name, "rejected", str(exc))
    state.last_success_at = now
    state.last_error = None
    state.consecutive_failures = 0
    log.info("Feed %s işlendi: %s", cfg.name, report.stats)
    return report


def stale_feeds(session: Session, configs: dict[str, FeedConfig], now: datetime | None = None) -> list[str]:
    """Beklenen sürede başarıyla güncellenmemiş feed'lerin listesi (izleme/uyarı için)."""
    now = now or utcnow()
    out = []
    for cfg in configs.values():
        if not cfg.enabled:
            continue
        state = session.get(FeedState, cfg.name)
        last = as_utc(state.last_success_at) if state else None
        if last is None or now - last > timedelta(hours=cfg.stale_after_hours):
            out.append(cfg.name)
    return out
