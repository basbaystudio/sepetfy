"""Alembic ortamı. Bağlantı ``create_schema`` tarafından verilir; yoksa ayarlardaki URL kullanılır.

Not: Burada ``logging.config.fileConfig`` çağrılmaz; uygulamanın (gizli değer maskeleyen)
loglama ayarları korunur.
"""

from alembic import context
from sqlalchemy import create_engine

from sepetfy.database.models import Base

config = context.config
target_metadata = Base.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite'ta ALTER TABLE desteği için
        compare_type=True,
        # SQLite bağlantılarımız gerçek işlem yönetimiyle açılır (session.make_engine): DDL geri alınabilir
        transactional_ddl=True,
    )


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    from sepetfy.config import get_settings
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        _configure(conn)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
