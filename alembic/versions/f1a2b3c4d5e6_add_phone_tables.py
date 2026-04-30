"""add phone/voice tables (phone_numbers, call_events, voicemails, phone_business_hours, phone_routing_rules)

Revision ID: f1a2b3c4d5e6
Revises: e7a8b9c0d1e2
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'phone_numbers' not in existing_tables:
        op.create_table(
            'phone_numbers',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
            sa.Column('twilio_sid', sa.String(100), nullable=False),
            sa.Column('phone_number', sa.String(32), nullable=False),
            sa.Column('friendly_name', sa.String(255), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_phone_numbers_org_id', 'phone_numbers', ['org_id'])
        op.create_index('ix_phone_numbers_twilio_sid', 'phone_numbers', ['twilio_sid'], unique=True)
        op.create_index('ix_phone_numbers_phone_number', 'phone_numbers', ['phone_number'], unique=True)

    if 'phone_business_hours' not in existing_tables:
        op.create_table(
            'phone_business_hours',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
            sa.Column('day_of_week', sa.Integer(), nullable=False),  # 0=Mon, 6=Sun
            sa.Column('open_time', sa.String(5), nullable=True),   # "09:00", null = closed
            sa.Column('close_time', sa.String(5), nullable=True),  # "17:00"
            sa.Column('timezone', sa.String(100), nullable=False, server_default='America/Edmonton'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_phone_business_hours_org_id', 'phone_business_hours', ['org_id'])

    if 'phone_routing_rules' not in existing_tables:
        op.create_table(
            'phone_routing_rules',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
            sa.Column('ring_owner_first', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('owner_phone', sa.String(32), nullable=False, server_default=''),
            sa.Column('ring_timeout_seconds', sa.Integer(), nullable=False, server_default='20'),
            sa.Column('send_caller_confirmation', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('voicemail_greeting', sa.Text(), nullable=True),
            sa.Column('after_hours_greeting', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_phone_routing_rules_org_id', 'phone_routing_rules', ['org_id'], unique=True)

    if 'call_events' not in existing_tables:
        op.create_table(
            'call_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
            sa.Column('twilio_call_sid', sa.String(64), nullable=False),
            sa.Column('from_number', sa.String(32), nullable=False),
            sa.Column('to_number', sa.String(32), nullable=False),
            sa.Column('from_city', sa.String(100), nullable=True),
            sa.Column('from_state', sa.String(100), nullable=True),
            sa.Column('direction', sa.String(20), nullable=False, server_default='inbound'),
            sa.Column('status', sa.String(30), nullable=False, server_default='ringing'),
            sa.Column('dial_status', sa.String(30), nullable=True),
            sa.Column('answered_by_owner', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('duration_seconds', sa.Integer(), nullable=True),
            sa.Column('is_after_hours', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_call_events_org_id', 'call_events', ['org_id'])
        op.create_index('ix_call_events_twilio_call_sid', 'call_events', ['twilio_call_sid'], unique=True)
        op.create_index('ix_call_events_from_number', 'call_events', ['from_number'])
        op.create_index('ix_call_events_to_number', 'call_events', ['to_number'])

    if 'voicemails' not in existing_tables:
        op.create_table(
            'voicemails',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('call_event_id', sa.Integer(), sa.ForeignKey('call_events.id'), nullable=False),
            sa.Column('twilio_recording_sid', sa.String(64), nullable=False),
            sa.Column('recording_url', sa.String(500), nullable=False),
            sa.Column('recording_duration', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('transcript', sa.Text(), nullable=True),
            sa.Column('transcript_confidence', sa.Float(), nullable=True),
            sa.Column('transcription_status', sa.String(20), nullable=False, server_default='pending'),
            sa.Column('classified_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_voicemails_call_event_id', 'voicemails', ['call_event_id'])
        op.create_index('ix_voicemails_twilio_recording_sid', 'voicemails', ['twilio_recording_sid'], unique=True)

    leads_columns = [col['name'] for col in inspector.get_columns('leads')]
    if 'call_event_id' not in leads_columns:
        with op.batch_alter_table('leads') as batch_op:
            batch_op.add_column(sa.Column('call_event_id', sa.Integer(), nullable=True))
        existing_indexes = [idx['name'] for idx in inspector.get_indexes('leads')]
        if 'ix_leads_call_event_id' not in existing_indexes:
            op.create_index('ix_leads_call_event_id', 'leads', ['call_event_id'])


def downgrade() -> None:
    with op.batch_alter_table('leads') as batch_op:
        batch_op.drop_index('ix_leads_call_event_id')
        batch_op.drop_column('call_event_id')

    op.drop_index('ix_voicemails_twilio_recording_sid', table_name='voicemails')
    op.drop_index('ix_voicemails_call_event_id', table_name='voicemails')
    op.drop_table('voicemails')

    op.drop_index('ix_call_events_to_number', table_name='call_events')
    op.drop_index('ix_call_events_from_number', table_name='call_events')
    op.drop_index('ix_call_events_twilio_call_sid', table_name='call_events')
    op.drop_index('ix_call_events_org_id', table_name='call_events')
    op.drop_table('call_events')

    op.drop_index('ix_phone_routing_rules_org_id', table_name='phone_routing_rules')
    op.drop_table('phone_routing_rules')

    op.drop_index('ix_phone_business_hours_org_id', table_name='phone_business_hours')
    op.drop_table('phone_business_hours')

    op.drop_index('ix_phone_numbers_phone_number', table_name='phone_numbers')
    op.drop_index('ix_phone_numbers_twilio_sid', table_name='phone_numbers')
    op.drop_index('ix_phone_numbers_org_id', table_name='phone_numbers')
    op.drop_table('phone_numbers')
