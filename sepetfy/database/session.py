"""Veritabanı motoru ve oturum yönetimi."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from sepetfy.database.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if ":memory:" not in url:
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

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


_APPEND_ONLY_TRIGGERS = {
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


def create_schema(engine: Engine | None = None) -> None:
    """Tabloları oluşturur ve onay kayıtlarını DB seviyesinde değiştirilemez yapar."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _APPEND_ONLY_TRIGGERS.get(engine.dialect.name, []):
            conn.execute(text(stmt))


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
