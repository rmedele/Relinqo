"""add scheduling, booking, and photo models

Revision ID: e7a8b9c0d1e2
Revises: c69fc6b8b1c2
Create Date: 2026-04-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7a8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'c69fc6b8b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- New tables ---

    op.create_table(
        'lead_photos',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False, index=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=False, index=True),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('stored_filename', sa.String(255), nullable=False),
        sa.Column('file_path', sa.String(500), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('ai_analysis', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        'schedule_availability',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False, index=True),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.String(5), nullable=False),
        sa.Column('end_time', sa.String(5), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        'bookings',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False, index=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=True, index=True),
        sa.Column('token', sa.String(64), nullable=False, unique=True, index=True),
        sa.Column('slot_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('slot_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('customer_name', sa.String(255), nullable=False),
        sa.Column('customer_email', sa.String(255), nullable=False),
        sa.Column('customer_phone', sa.String(100), nullable=True),
        sa.Column('customer_notes', sa.Text(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, default='confirmed'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    )

    # --- Add columns to existing tables ---

    with op.batch_alter_table('leads') as batch_op:
        batch_op.add_column(sa.Column('booking_token', sa.String(64), nullable=True))
        batch_op.create_index('ix_leads_booking_token', ['booking_token'], unique=True)

    with op.batch_alter_table('org_settings') as batch_op:
        batch_op.add_column(sa.Column('scheduling_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        batch_op.add_column(sa.Column('scheduling_slot_duration', sa.Integer(), nullable=False, server_default=sa.text('60')))
        batch_op.add_column(sa.Column('scheduling_buffer_minutes', sa.Integer(), nullable=False, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('scheduling_max_days_ahead', sa.Integer(), nullable=False, server_default=sa.text('7')))


def downgrade() -> None:
    with op.batch_alter_table('org_settings') as batch_op:
        batch_op.drop_column('scheduling_max_days_ahead')
        batch_op.drop_column('scheduling_buffer_minutes')
        batch_op.drop_column('scheduling_slot_duration')
        batch_op.drop_column('scheduling_enabled')

    with op.batch_alter_table('leads') as batch_op:
        batch_op.drop_index('ix_leads_booking_token')
        batch_op.drop_column('booking_token')

    op.drop_table('bookings')
    op.drop_table('schedule_availability')
    op.drop_table('lead_photos')
