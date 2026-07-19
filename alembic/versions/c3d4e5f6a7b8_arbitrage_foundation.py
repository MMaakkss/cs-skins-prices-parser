"""arbitrage foundation: USD-only, Numeric price, price_type, fees, icon_url, indexes

Revision ID: c3d4e5f6a7b8
Revises: b7e2f1a9c3d4
Create Date: 2026-07-20 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b7e2f1a9c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- marketplaces: fee reference read by the API ---
    op.add_column('marketplaces', sa.Column(
        'sell_fee_percent', sa.Numeric(6, 4), nullable=False, server_default='0'))
    op.add_column('marketplaces', sa.Column(
        'buy_fee_percent', sa.Numeric(6, 4), nullable=False, server_default='0'))
    op.add_column('marketplaces', sa.Column(
        'payout_withdrawable', sa.Boolean(), nullable=False, server_default=sa.true()))

    # Seed / update known marketplaces (idempotent).
    op.execute("""
        INSERT INTO marketplaces (name, url, sell_fee_percent, buy_fee_percent, payout_withdrawable)
        VALUES
            ('steam',   'https://steamcommunity.com/market/', 0.1304, 0, false),
            ('dmarket', 'https://dmarket.com/',               0.05,   0, true)
        ON CONFLICT (name) DO UPDATE SET
            sell_fee_percent    = EXCLUDED.sell_fee_percent,
            buy_fee_percent     = EXCLUDED.buy_fee_percent,
            payout_withdrawable = EXCLUDED.payout_withdrawable
    """)

    # --- items: preview image + catalog filter index ---
    op.add_column('items', sa.Column('icon_url', sa.String(500), nullable=True))
    op.create_index('ix_items_weapon_exterior', 'items', ['weapon', 'exterior'])

    # --- price_records: money as Numeric, USD-only, price_type, latest index ---
    op.alter_column(
        'price_records', 'price',
        existing_type=sa.Float(),
        type_=sa.Numeric(14, 4),
        existing_nullable=False,
        postgresql_using='price::numeric(14,4)',
    )
    op.add_column('price_records', sa.Column(
        'price_type', sa.String(8), nullable=False, server_default='ask'))

    # Everything is USD now — drop the currency column.
    op.drop_column('price_records', 'currency')

    # UNIQUE must include price_type so ask/bid can share a recorded_at later.
    op.drop_constraint('uq_price_snapshot', 'price_records', type_='unique')
    op.create_unique_constraint(
        'uq_price_snapshot', 'price_records',
        ['item_id', 'marketplace_id', 'price_type', 'recorded_at'],
    )

    # "Latest price per (item, marketplace, price_type)" support for the API.
    op.execute(
        "CREATE INDEX ix_price_records_latest ON price_records "
        "(item_id, marketplace_id, price_type, recorded_at DESC)"
    )


def downgrade() -> None:
    op.drop_index('ix_price_records_latest', table_name='price_records')

    op.drop_constraint('uq_price_snapshot', 'price_records', type_='unique')
    op.create_unique_constraint(
        'uq_price_snapshot', 'price_records',
        ['item_id', 'marketplace_id', 'recorded_at'],
    )

    op.add_column('price_records', sa.Column(
        'currency', sa.String(10), nullable=False, server_default='USD'))
    op.drop_column('price_records', 'price_type')
    op.alter_column(
        'price_records', 'price',
        existing_type=sa.Numeric(14, 4),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using='price::double precision',
    )

    op.drop_index('ix_items_weapon_exterior', table_name='items')
    op.drop_column('items', 'icon_url')

    op.drop_column('marketplaces', 'payout_withdrawable')
    op.drop_column('marketplaces', 'buy_fee_percent')
    op.drop_column('marketplaces', 'sell_fee_percent')
