"""seed the market.csgo.com marketplace with its fee reference

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Seller commission is 5%; sale proceeds are withdrawable to real money.
    op.execute("""
        INSERT INTO marketplaces (name, url, sell_fee_percent, buy_fee_percent, payout_withdrawable)
        VALUES ('market_csgo', 'https://market.csgo.com/', 0.05, 0, true)
        ON CONFLICT (name) DO UPDATE SET
            url                 = EXCLUDED.url,
            sell_fee_percent    = EXCLUDED.sell_fee_percent,
            buy_fee_percent     = EXCLUDED.buy_fee_percent,
            payout_withdrawable = EXCLUDED.payout_withdrawable
    """)


def downgrade() -> None:
    # Only drop the row when nothing references it — collected price history
    # must survive a downgrade rather than block it.
    op.execute("""
        DELETE FROM marketplaces m
        WHERE m.name = 'market_csgo'
          AND NOT EXISTS (
              SELECT 1 FROM price_records p WHERE p.marketplace_id = m.id
          )
    """)
