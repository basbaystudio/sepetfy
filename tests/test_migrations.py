"""Şema göçleri: SQLite her zaman, PostgreSQL ise SEPETFY_TEST_PG_URL tanımlıysa test edilir.

    SEPETFY_TEST_PG_URL=postgresql+psycopg://postgres:test@127.0.0.1:54329/sepetfy_test pytest
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from sepetfy.database.models import Base
from sepetfy.database.session import _alembic_config, create_schema, current_revision, make_engine

HEAD = "0002"
PG_URL = os.environ.get("SEPETFY_TEST_PG_URL")


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    else:
        if not PG_URL:
            pytest.skip("SEPETFY_TEST_PG_URL tanımlı değil")
        eng = create_engine(PG_URL)
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    yield eng
    eng.dispose()


def _upgrade_to(engine, rev):
    with engine.begin() as conn:
        command.upgrade(_alembic_config(conn), rev)


def test_fresh_database_reaches_head_and_matches_models(engine):
    create_schema(engine)
    assert current_revision(engine) == HEAD
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    if engine.dialect.name == "sqlite":
        # SQLite VARCHAR uzunluğunu uygulamaz; göç bu değişikliği bilinçli olarak atlar
        diffs = [d for d in diffs if not (isinstance(d, list) and d and d[0][0] == "modify_type")]
    assert diffs == [], f"Modeller ile göçler uyuşmuyor: {diffs}"


def _seed_phase1_rows(conn):
    conn.execute(text("INSERT INTO stores (id, code, name, base_url, access_method, access_status, enabled, "
                      "check_interval_min, notes, created_at) VALUES (1,'s','S','', 'manual','verified', "
                      ":f, 15, '', CURRENT_TIMESTAMP)"), {"f": False})
    conn.execute(text("INSERT INTO products (id, store_id, external_id, title, url, created_at, updated_at) "
                      "VALUES (1, 1, 'X', 'Ürün', 'https://e.com', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    conn.execute(text("INSERT INTO price_snapshots (id, product_id, price, currency, coupon_verified, source, "
                      "observed_at) VALUES (1, 1, 100, 'TRY', :f, 't', CURRENT_TIMESTAMP)"), {"f": False})


def test_phase1_database_without_version_table_is_upgraded_keeping_data(engine):
    """FAZ 1'de göç aracı olmadan oluşturulmuş, içinde veri olan veritabanı."""
    _upgrade_to(engine, "0001")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        _seed_phase1_rows(conn)

    create_schema(engine)

    assert current_revision(engine) == HEAD
    with engine.connect() as conn:
        row = conn.execute(text("SELECT active, missing_count, title FROM products WHERE id=1")).one()
        assert bool(row.active) is True and row.missing_count == 0 and row.title == "Ürün"
        assert conn.execute(text("SELECT count(*) FROM price_snapshots")).scalar() == 1
        assert "feed_states" in inspect(conn).get_table_names()


def test_create_schema_is_idempotent(engine):
    create_schema(engine)
    create_schema(engine)
    assert current_revision(engine) == HEAD


def test_downgrade_and_upgrade_roundtrip(engine):
    create_schema(engine)
    with engine.begin() as conn:
        _seed_phase1_rows(conn)
    with engine.begin() as conn:
        command.downgrade(_alembic_config(conn), "0001")
    assert current_revision(engine) == "0001"
    _upgrade_to(engine, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM products")).scalar() == 1


def test_approval_records_append_only_after_migrations(engine):
    create_schema(engine)
    with engine.begin() as conn:
        _seed_phase1_rows(conn)
        conn.execute(text("INSERT INTO deals (id, product_id, snapshot_id, fingerprint, discount_verified, status, "
                          "created_at) VALUES (1, 1, 1, 'fp', :f, 'new', CURRENT_TIMESTAMP)"), {"f": False})
        conn.execute(text("INSERT INTO content_items (id, deal_id, platform, kind, body, version, content_hash, "
                          "status, created_at) VALUES (1, 1, 'telegram', 'm', 'b', 1, 'h', 'approved', "
                          "CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO approval_records (id, content_id, decision, platforms, content_hash, "
                          "actor_telegram_id, decided_at) VALUES (1, 1, 'approve', '[]', 'h', 5, CURRENT_TIMESTAMP)"))
    for stmt in ("UPDATE approval_records SET note='x'", "DELETE FROM approval_records"):
        with pytest.raises(DBAPIError), engine.begin() as conn:
            conn.execute(text(stmt))


# ---------------------------------------------------------------- SQLite işlem güvenliği
def test_sqlite_ddl_is_transactional(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    with pytest.raises(RuntimeError), eng.begin() as conn:
        conn.execute(text("CREATE TABLE x (id INTEGER)"))
        raise RuntimeError("yarıda kesildi")
    assert "x" not in inspect(eng).get_table_names()


def test_sqlite_savepoint_really_rolls_back(tmp_path):
    from sqlalchemy.orm import Session

    from sepetfy.database.models import Store

    eng = make_engine(f"sqlite:///{tmp_path / 's.db'}")
    create_schema(eng)
    with Session(eng) as s, s.begin():
        s.add(Store(code="dis", name="Dış", access_method="m", access_status="v"))
        with pytest.raises(RuntimeError), s.begin_nested():
            s.add(Store(code="ic", name="İç", access_method="m", access_status="v"))
            s.flush()
            raise RuntimeError
    with eng.connect() as c:
        assert [r[0] for r in c.execute(text("SELECT code FROM stores"))] == ["dis"]


def test_half_stamped_database_recovers(tmp_path):
    """Gerçekte görülen durum: alembic_version tablosu var ama boş; feed_states önceden oluşmuş."""
    eng = make_engine(f"sqlite:///{tmp_path / 'h.db'}")
    _upgrade_to(eng, "0001")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("CREATE TABLE feed_states (name VARCHAR(64) PRIMARY KEY, store_id INTEGER NOT NULL, "
                          "etag VARCHAR(256), last_modified VARCHAR(64), content_hash VARCHAR(64), "
                          "last_checked_at DATETIME, last_success_at DATETIME, last_item_count INTEGER, "
                          "last_stats JSON, last_error TEXT, consecutive_failures INTEGER NOT NULL)"))
        _seed_phase1_rows(conn)
    create_schema(eng)
    assert current_revision(eng) == HEAD
    with eng.connect() as conn:
        assert conn.execute(text("SELECT active FROM products")).scalar() in (1, True)
