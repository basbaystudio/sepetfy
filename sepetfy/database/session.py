"""Veritabanı motoru ve oturum yönetimi."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if ":memory:" not in url:
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            # pysqlite'ın kendi işlem yönetimini kapat: aksi hâlde DDL (CREATE/ALTER) işleme
            # dahil olmaz ve SAVEPOINT doğru çalışmaz. İşlemleri aşağıda biz başlatıyoruz.
            dbapi_conn.isolation_level = None
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn):
            conn.exec_driver_sql("BEGIN")

        return engine
    return create_engine(url, pool_pre_ping=True)


def init_engine(url: str) -> Engine:
    global _engine, _SessionLocal
    _engine = make_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        from sepetfy.config import get_settings
        init_engine(get_settings().database_url)
    assert _engine is not None
    return _engine


APPEND_ONLY_TRIGGERS = {
    "sqlite": [
        """CREATE TRIGGER IF NOT EXISTS approval_no_update BEFORE UPDATE ON approval_records
           BEGIN SELECT RAISE(ABORT, 'approval_records is append-only'); END""",
        """CREATE TRIGGER IF NOT EXISTS approval_no_delete BEFORE DELETE ON approval_records
           BEGIN SELECT RAISE(ABORT, 'approval_records is append-only'); END""",
    ],
    "postgresql": [
        """CREATE OR REPLACE FUNCTION sepetfy_append_only() RETURNS trigger AS $$
           BEGIN RAISE EXCEPTION 'approval_records is append-only'; END; $$ LANGUAGE plpgsql""",
        "DROP TRIGGER IF EXISTS approval_append_only ON approval_records",
        """CREATE TRIGGER approval_append_only BEFORE UPDATE OR DELETE ON approval_records
           FOR EACH ROW EXECUTE FUNCTION sepetfy_append_only()""",
    ],
}


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BASELINE_REVISION = "0001"


def _alembic_config(connection):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["connection"] = connection
    return cfg


def create_schema(engine: Engine | None = None) -> None:
    """Şemayı en güncel sürüme getirir (Alembic göçleri). Veriler korunur.

    FAZ 1'de göç aracı olmadan oluşturulmuş veritabanları temel sürüm olarak işaretlenip yükseltilir.
    """
    from alembic import command

    engine = engine or get_engine()
    logging.getLogger("alembic").setLevel(logging.WARNING)
    with engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        cfg = _alembic_config(conn)
        versioned = "alembic_version" in tables and conn.execute(
            text("SELECT count(*) FROM alembic_version")).scalar()
        if "stores" in tables and not versioned:
            command.stamp(cfg, BASELINE_REVISION)
            log.info("Göç aracından önceki veritabanı temel sürüm (%s) olarak işaretlendi", BASELINE_REVISION)
        before = MigrationContext.configure(conn).get_current_revision()
        command.upgrade(cfg, "head")
        after = MigrationContext.configure(conn).get_current_revision()
        if before != after:
            log.info("Veritabanı şeması yükseltildi: %s -> %s", before or "boş", after)


def current_revision(engine: Engine | None = None) -> str | None:
    with (engine or get_engine()).connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping() -> bool:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT 1")).scalar() == 1
