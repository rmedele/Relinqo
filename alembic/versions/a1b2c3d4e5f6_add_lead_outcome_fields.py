"""add lead outcome fields

Revision ID: a1b2c3d4e5f6
Revises: f99f35365c7e
Create Date: 2026-04-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '0c61520c9265'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('outcome', sa.String(50), nullable=True))
    op.add_column('leads', sa.Column('outcome_notes', sa.Text(), nullable=True))
    op.add_column('leads', sa.Column('outcome_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_leads_outcome', 'leads', ['outcome'])


def downgrade() -> None:
    op.drop_index('ix_leads_outcome', 'leads')
    op.drop_column('leads', 'outcome_at')
    op.drop_column('leads', 'outcome_notes')
    op.drop_column('leads', 'outcome')
