"""sales_stats: per-item sales volume as the marketplace reports it

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sales_stats',
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('marketplace_id', sa.Integer(), sa.ForeignKey('marketplaces.id'), nullable=False),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sales_7d', sa.Integer(), nullable=False),
        sa.Column('sales_30d', sa.Integer(), nullable=False),
        sa.Column('median_7d', sa.Numeric(14, 4), nullable=True),
        sa.Column('median_30d', sa.Numeric(14, 4), nullable=True),
        sa.Column('capped', sa.Boolean(), nullable=False),
        sa.Column('oldest_sale', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('item_id', 'marketplace_id', 'as_of', name='sales_stats_pkey'),
    )


def downgrade() -> None:
    op.drop_table('sales_stats')
