"""parser_state: resume point for passes cut short by a rate limit

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'parser_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('marketplace', sa.String(50), nullable=False),
        sa.Column('filters_key', sa.String(500), nullable=False),
        sa.Column('next_start', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('marketplace', 'filters_key', name='uq_parser_state'),
    )


def downgrade() -> None:
    op.drop_table('parser_state')
