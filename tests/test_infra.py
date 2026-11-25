from __future__ import annotations

import gzip
import logging
import sqlite3
from pathlib import Path

import pytest

from sepetfy.app.errors import BudgetExceeded, ExternalServiceError, ServiceUnavailable
from sepetfy.app.logging import MASK, RedactingFilter, redact
from sepetfy.app.resilience import CircuitBreaker
from sepetfy.config import Settings
from sepetfy.database import session_scope


# ---------------------------------------------------------------- ayarlar
def test_settings_parsing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_ADMIN_USER_IDS=1, 22 ,333\n"
        "ANTHROPIC_API_KEY=\n"
        "DATABASE_URL=sqlite:///data/x.db\n"
        "MONTHLY_BUDGET_USD=25   # yorum\n"
    )
    s = Settings(_env_file=env)
    assert s.admin_user_ids == frozenset({1, 22, 333})
    assert s.anthropic_api_key is None  # boş değer = yapılandırılmamış
    assert Path(s.database_url.removeprefix("sqlite:///")).is_absolute()  # mutlak yola çevrildi (Windows'ta C:\\...)
    assert s.monthly_budget_usd == 25
    assert s.publishing_enabled is False and s.dry_run is True  # güvenli varsayılanlar


def test_secret_not_in_repr(tmp_path):
    s = Settings(_env_file=None, anthropic_api_key="sk-ant-supersecretvalue123")
    assert "supersecret" not in repr(s)
    assert "supersecret" not in str(s.model_dump())


# ---------------------------------------------------------------- log maskeleme
@pytest.mark.parametrize("raw", [
    "token=123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawq",
    "key sk-ant-api03-abcdefghijklmnop",
    "AIzaSyD-1234567890abcdefghijklmnopqrstu",
    "Authorization: Bearer abcdef1234567890xyz",
    "api_key=myrealkey9",
])
def test_redact_patterns(raw):
    out = redact(raw)
    assert MASK in out


def test_redacting_filter_known_secret():
    f = RedactingFilter(["s3cr3t-value"])
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "using %s now", ("s3cr3t-value",), None)
    f.filter(rec)
    assert "s3cr3t" not in rec.getMessage()


# ---------------------------------------------------------------- devre kesici
def test_circuit_breaker_opens_and_recovers():
    now = [0.0]
    cb = CircuitBreaker("svc", failure_threshold=2, cooldown_sec=10, clock=lambda: now[0])

    def boom():
        raise ExternalServiceError("svc", "down")

    with pytest.raises(ExternalServiceError):
        cb.call(boom, retries=5, sleep=lambda _: None)
    assert cb.is_open
    with pytest.raises(ServiceUnavailable):
        cb.call(lambda: "ok")
    now[0] = 11
    assert cb.call(lambda: "ok") == "ok"
    assert not cb.is_open


def test_non_retryable_error_not_retried():
    calls = []
    cb = CircuitBreaker("svc", failure_threshold=10)

    def bad():
        calls.append(1)
        raise ExternalServiceError("svc", "400", retryable=False)

    with pytest.raises(ExternalServiceError):
        cb.call(bad, retries=3, sleep=lambda _: None)
    assert len(calls) == 1


# ---------------------------------------------------------------- bütçe
def test_budget_guard(settings):
    from sepetfy.analytics.costs import ensure_budget, record_cost, set_budget, summary

    with session_scope() as s:
        record_cost(s, "claude", "analyze", 7.5)
        st = ensure_budget(s, settings, 0.6)  # 8.1 / 10 -> uyarı, engel yok
        assert st.warn and not st.exceeded
        with pytest.raises(BudgetExceeded):
            ensure_budget(s, settings, 3.0)
        set_budget(s, 50)
        assert not ensure_budget(s, settings, 3.0).warn
        sm = summary(s, settings)
        assert sm["by_service"] == {"claude": 7.5}
        assert sm["budget_usd"] == 50


# ---------------------------------------------------------------- yedekleme
def test_backup_and_rotation(settings):
    from sepetfy.database.backup import backup_database
    from sepetfy.database.seed import seed_stores

    with session_scope() as s:
        seed_stores(s)
    out = backup_database(settings.database_url, settings.backup_dir, keep=2)
    raw = gzip.decompress(out.read_bytes())
    if settings.database_url.startswith("postgresql"):
        dump = raw.decode()
        assert "CREATE TABLE public.stores" in dump and "Trendyol" in dump
    else:
        restored = settings.backup_dir / "restored.db"
        restored.write_bytes(raw)
        n = sqlite3.connect(restored).execute("SELECT count(*) FROM stores").fetchone()[0]
        assert n > 0

    for i in range(3):
        (settings.backup_dir / f"sepetfy-2000010{i}T000000Z.db.gz").write_bytes(b"x")
    backup_database(settings.database_url, settings.backup_dir, keep=2)
    assert len(list(settings.backup_dir.glob("sepetfy-*.gz"))) == 2


def test_seed_is_idempotent_and_disabled(settings):
    from sqlalchemy import select

    from sepetfy.database.models import Store
    from sepetfy.database.seed import seed_stores

    with session_scope() as s:
        first = seed_stores(s)
    with session_scope() as s:
        assert seed_stores(s) == 0
        stores = s.scalars(select(Store)).all()
        assert len(stores) == first
        assert not any(st.enabled for st in stores)  # doğrulanmadan hiçbir kaynak açık değil


# ---------------------------------------------------------------- Windows konsol kodlaması
def test_cli_output_survives_legacy_windows_codepage():
    """Windows'ta yönlendirilmiş çıktı cp1252 olur; Türkçe karakter UnicodeEncodeError vermemeli."""
    import io

    from sepetfy.cli import ensure_utf8_output

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    with pytest.raises(UnicodeEncodeError):
        stream.write("kapalı ş ğ")
        stream.flush()
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    ensure_utf8_output([stream])
    stream.write("kapalı ş ğ ✅")
    stream.flush()
    assert stream.buffer.getvalue().decode("utf-8") == "kapalı ş ğ ✅"
