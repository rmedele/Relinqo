"""add sms_notifications table for outbound SMS tracking + dedup

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'sms_notifications' not in existing_tables:
        op.create_table(
            'sms_notifications',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
            sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=True),
            sa.Column('call_event_id', sa.Integer(), sa.ForeignKey('call_events.id'), nullable=True),
            sa.Column('direction', sa.String(10), nullable=False, server_default='outbound'),
            sa.Column('to_number', sa.String(32), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('twilio_message_sid', sa.String(64), nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
            sa.Column('purpose', sa.String(40), nullable=False),  # owner_alert | caller_confirmation | approval_request
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_sms_notifications_org_id', 'sms_notifications', ['org_id'])
        op.create_index('ix_sms_notifications_to_number', 'sms_notifications', ['to_number'])
        op.create_index('ix_sms_notifications_purpose', 'sms_notifications', ['purpose'])
        op.create_index('ix_sms_notifications_created_at', 'sms_notifications', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_sms_notifications_created_at', table_name='sms_notifications')
    op.drop_index('ix_sms_notifications_purpose', table_name='sms_notifications')
    op.drop_index('ix_sms_notifications_to_number', table_name='sms_notifications')
    op.drop_index('ix_sms_notifications_org_id', table_name='sms_notifications')
    op.drop_table('sms_notifications')
