"""create core tables (leads, followups, owner_alerts, lead_activities, inbox_messages)

Revision ID: 2d4f8a1b3c9e
Revises: 13807a6fdb06
Create Date: 2026-03-27 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d4f8a1b3c9e'
down_revision: Union[str, Sequence[str], None] = '13807a6fdb06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create core application tables."""

    op.create_table(
        'leads',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('sender_name', sa.String(255), nullable=True),
        sa.Column('sender_email', sa.String(255), nullable=False, index=True),
        sa.Column('subject', sa.String(500), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('phone', sa.String(100), nullable=True),
        sa.Column('location', sa.String(255), nullable=True),
        sa.Column('category', sa.String(50), nullable=False, server_default='general_inquiry'),
        sa.Column('urgency_score', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('recommended_reply', sa.Text(), nullable=True),
        sa.Column('owner_alert_needed', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('status', sa.String(50), nullable=False, server_default='new'),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('next_step', sa.String(255), nullable=True),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('thread_id', sa.String(255), nullable=True),
        sa.Column('send_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'followups',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=False),
        sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
        sa.Column('followup_type', sa.String(50), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='scheduled'),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'owner_alerts',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=False),
        sa.Column('alert_type', sa.String(50), nullable=False),
        sa.Column('sent_to', sa.String(255), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='queued'),
    )

    op.create_table(
        'lead_activities',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=False, index=True),
        sa.Column('activity_type', sa.String(50), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        'inbox_messages',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('source', sa.String(50), nullable=False, server_default='imap'),
        sa.Column('external_id', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    """Drop core application tables."""
    op.drop_table('inbox_messages')
    op.drop_table('lead_activities')
    op.drop_table('owner_alerts')
    op.drop_table('followups')
    op.drop_table('leads')
