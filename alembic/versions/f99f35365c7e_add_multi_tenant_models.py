"""add multi-tenant models

Revision ID: f99f35365c7e
Revises: 0c61520c9265
Create Date: 2026-04-01
"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from passlib.hash import bcrypt


revision: str = 'f99f35365c7e'
down_revision: Union[str, Sequence[str], None] = '2d4f8a1b3c9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Create new tables ---
    op.create_table(
        'organizations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False, unique=True),
        sa.Column('api_key', sa.String(64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_organizations_slug', 'organizations', ['slug'])
    op.create_index('ix_organizations_api_key', 'organizations', ['api_key'])

    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('role', sa.String(20), nullable=False, server_default='member'),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_index('ix_users_org_id', 'users', ['org_id'])

    op.create_table(
        'org_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False, unique=True),
        # SMTP
        sa.Column('smtp_host', sa.String(255), nullable=False, server_default=''),
        sa.Column('smtp_port', sa.Integer(), nullable=False, server_default='587'),
        sa.Column('smtp_username', sa.String(255), nullable=False, server_default=''),
        sa.Column('smtp_password', sa.String(255), nullable=False, server_default=''),
        sa.Column('smtp_use_tls', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('smtp_from_email', sa.String(255), nullable=False, server_default=''),
        # IMAP
        sa.Column('imap_host', sa.String(255), nullable=False, server_default='imap.gmail.com'),
        sa.Column('imap_port', sa.Integer(), nullable=False, server_default='993'),
        sa.Column('imap_username', sa.String(255), nullable=False, server_default=''),
        sa.Column('imap_password', sa.String(255), nullable=False, server_default=''),
        sa.Column('imap_mailbox', sa.String(100), nullable=False, server_default='INBOX'),
        sa.Column('imap_search_criteria', sa.String(100), nullable=False, server_default='UNSEEN'),
        sa.Column('inbox_poll_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        # Business profile
        sa.Column('business_name', sa.String(255), nullable=False, server_default=''),
        sa.Column('business_services', sa.Text(), nullable=False, server_default=''),
        sa.Column('business_area', sa.String(255), nullable=False, server_default=''),
        sa.Column('business_hours', sa.String(255), nullable=False, server_default='Mon-Fri 8am-5pm'),
        sa.Column('business_phone', sa.String(100), nullable=False, server_default=''),
        sa.Column('business_tone', sa.String(255), nullable=False, server_default='friendly and professional'),
        sa.Column('business_reply_signature', sa.Text(), nullable=False, server_default=''),
        # Twilio
        sa.Column('twilio_account_sid', sa.String(100), nullable=False, server_default=''),
        sa.Column('twilio_auth_token', sa.String(100), nullable=False, server_default=''),
        sa.Column('twilio_from_number', sa.String(50), nullable=False, server_default=''),
        sa.Column('sms_alert_to_number', sa.String(50), nullable=False, server_default=''),
        # Behavior
        sa.Column('human_review', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('auto_send_confidence_threshold', sa.Float(), nullable=False, server_default='0.85'),
        sa.Column('forwarding_token', sa.String(100), nullable=False, server_default=''),
        sa.Column('owner_alert_email', sa.String(255), nullable=False, server_default=''),
        sa.Column('digest_to_email', sa.String(255), nullable=False, server_default=''),
        sa.Column('default_timezone', sa.String(100), nullable=False, server_default='America/Edmonton'),
    )

    # --- Seed default org from env vars ---
    import os
    org_name = os.environ.get('BUSINESS_NAME', '') or 'Default'
    api_key = secrets.token_hex(32)

    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO organizations (id, name, slug, api_key) VALUES (1, :name, 'default', :api_key)"
    ), {"name": org_name, "api_key": api_key})

    # Seed OrgSettings from env vars
    env = {
        'smtp_host': os.environ.get('SMTP_HOST', ''),
        'smtp_port': int(os.environ.get('SMTP_PORT', '587')),
        'smtp_username': os.environ.get('SMTP_USERNAME', ''),
        'smtp_password': os.environ.get('SMTP_PASSWORD', ''),
        'smtp_use_tls': os.environ.get('SMTP_USE_TLS', 'true').lower() == 'true',
        'smtp_from_email': os.environ.get('SMTP_FROM_EMAIL', ''),
        'imap_host': os.environ.get('IMAP_HOST', 'imap.gmail.com'),
        'imap_port': int(os.environ.get('IMAP_PORT', '993')),
        'imap_username': os.environ.get('IMAP_USERNAME', ''),
        'imap_password': os.environ.get('IMAP_PASSWORD', ''),
        'imap_mailbox': os.environ.get('IMAP_MAILBOX', 'INBOX'),
        'imap_search_criteria': os.environ.get('IMAP_SEARCH_CRITERIA', 'UNSEEN'),
        'inbox_poll_enabled': os.environ.get('INBOX_POLL_ENABLED', 'false').lower() == 'true',
        'business_name': os.environ.get('BUSINESS_NAME', ''),
        'business_services': os.environ.get('BUSINESS_SERVICES', ''),
        'business_area': os.environ.get('BUSINESS_AREA', ''),
        'business_hours': os.environ.get('BUSINESS_HOURS', 'Mon-Fri 8am-5pm'),
        'business_phone': os.environ.get('BUSINESS_PHONE', ''),
        'business_tone': os.environ.get('BUSINESS_TONE', 'friendly and professional'),
        'business_reply_signature': os.environ.get('BUSINESS_REPLY_SIGNATURE', ''),
        'twilio_account_sid': os.environ.get('TWILIO_ACCOUNT_SID', ''),
        'twilio_auth_token': os.environ.get('TWILIO_AUTH_TOKEN', ''),
        'twilio_from_number': os.environ.get('TWILIO_FROM_NUMBER', ''),
        'sms_alert_to_number': os.environ.get('SMS_ALERT_TO_NUMBER', ''),
        'human_review': os.environ.get('HUMAN_REVIEW', 'true').lower() == 'true',
        'auto_send_confidence_threshold': float(os.environ.get('AUTO_SEND_CONFIDENCE_THRESHOLD', '0.85')),
        'forwarding_token': os.environ.get('FORWARDING_TOKEN', 'change-forwarding-token'),
        'owner_alert_email': os.environ.get('OWNER_ALERT_EMAIL', ''),
        'digest_to_email': os.environ.get('DIGEST_TO_EMAIL', ''),
        'default_timezone': os.environ.get('DEFAULT_TIMEZONE', 'America/Edmonton'),
    }
    cols = ', '.join(env.keys())
    placeholders = ', '.join(f':{k}' for k in env.keys())
    conn.execute(sa.text(
        f'INSERT INTO org_settings (org_id, {cols}) VALUES (1, {placeholders})'
    ), env)

    # Seed owner user
    owner_email = os.environ.get('OWNER_ALERT_EMAIL', '') or 'admin@leadrelay.local'
    owner_password = os.environ.get('REVIEW_PASSWORD', 'change-me')
    password_hash = bcrypt.hash(owner_password)
    conn.execute(sa.text(
        "INSERT INTO users (email, password_hash, display_name, role, org_id) "
        "VALUES (:email, :hash, 'Admin', 'owner', 1)"
    ), {"email": owner_email, "hash": password_hash})

    # --- Add org_id to existing tables (nullable first) ---
    tables = ['leads', 'followups', 'owner_alerts', 'lead_activities', 'inbox_messages']
    for table in tables:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('org_id', sa.Integer(), nullable=True))

    # Backfill
    for table in tables:
        conn.execute(sa.text(f'UPDATE {table} SET org_id = 1 WHERE org_id IS NULL'))

    # Make NOT NULL, add index + FK
    for table in tables:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column('org_id', nullable=False)
            batch_op.create_index(f'ix_{table}_org_id', ['org_id'])
            batch_op.create_foreign_key(f'fk_{table}_org_id', 'organizations', ['org_id'], ['id'])


def downgrade() -> None:
    tables = ['leads', 'followups', 'owner_alerts', 'lead_activities', 'inbox_messages']
    for table in tables:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f'fk_{table}_org_id', type_='foreignkey')
            batch_op.drop_index(f'ix_{table}_org_id')
            batch_op.drop_column('org_id')

    op.drop_table('org_settings')
    op.drop_table('users')
    op.drop_index('ix_organizations_api_key', 'organizations')
    op.drop_index('ix_organizations_slug', 'organizations')
    op.drop_table('organizations')
