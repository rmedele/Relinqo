"""add parent_lead_id for conversation threading

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('leads') as batch_op:
        batch_op.add_column(sa.Column('parent_lead_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_leads_parent_lead_id', ['parent_lead_id'])
        batch_op.create_foreign_key('fk_leads_parent_lead_id', 'leads', ['parent_lead_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('leads') as batch_op:
        batch_op.drop_constraint('fk_leads_parent_lead_id', type_='foreignkey')
        batch_op.drop_index('ix_leads_parent_lead_id')
        batch_op.drop_column('parent_lead_id')
