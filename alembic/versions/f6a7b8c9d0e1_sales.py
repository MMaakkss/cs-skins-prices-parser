"""sales: individual sales from live feeds, partitioned by day

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-06 00:00:00.000000

Only the parent table is created here. Daily partitions are created ahead of
time and dropped past retention by the feed process itself, so a fresh
database accepts no rows until the feed has run once.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sales',
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('marketplace_id', sa.Integer(), sa.ForeignKey('marketplaces.id'), nullable=False),
        sa.Column('sold_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('price', sa.Numeric(14, 4), nullable=False),
        sa.PrimaryKeyConstraint('item_id', 'marketplace_id', 'sold_at', 'price', name='sales_pkey'),
        postgresql_partition_by='RANGE (sold_at)',
    )


def downgrade() -> None:
    # Dropping a partitioned table drops its partitions with it.
    op.drop_table('sales')
