"""faz2 feed takibi

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24 22:18:43.670410
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


URL_COLUMNS = (("url", False), ("affiliate_url", True), ("image_url", True))


def upgrade() -> None:
    # Dayanıklılık: göç aracından önce kısmen oluşturulmuş veritabanlarında mevcut olanı atla
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    product_cols = {c["name"] for c in insp.get_columns("products")}

    if "feed_states" not in tables:
        _create_feed_states()
    # Mevcut ürün satırları için varsayılan değerlerle ekle (tabloyu yeniden kurmadan)
    if "active" not in product_cols:
        op.add_column('products', sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))
    if "missing_count" not in product_cols:
        op.add_column('products', sa.Column('missing_count', sa.Integer(), nullable=False, server_default='0'))

    # SQLite VARCHAR uzunluğu uygulamaz; yalnızca diğer veritabanlarında genişlet
    if op.get_bind().dialect.name != "sqlite":
        for col, nullable in URL_COLUMNS:
            op.alter_column('products', col, existing_type=sa.VARCHAR(length=1024),
                            type_=sa.String(length=2048), existing_nullable=nullable)


def _create_feed_states() -> None:
    op.create_table('feed_states',
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('store_id', sa.Integer(), nullable=False),
    sa.Column('etag', sa.String(length=256), nullable=True),
    sa.Column('last_modified', sa.String(length=64), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_item_count', sa.Integer(), nullable=True),
    sa.Column('last_stats', sa.JSON(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('consecutive_failures', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ),
    sa.PrimaryKeyConstraint('name')
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        for col, nullable in URL_COLUMNS:
            op.alter_column('products', col, existing_type=sa.String(length=2048),
                            type_=sa.VARCHAR(length=1024), existing_nullable=nullable)
    # SQLite'ta batch (tabloyu yeniden kurma) yabancı anahtarlar yüzünden başarısız olur;
    # SQLite 3.35+ yerel DROP COLUMN destekler.
    for col in ("missing_count", "active"):
        op.drop_column('products', col)
    op.drop_table('feed_states')
