from __future__ import annotations

import logging
from threading import Lock

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

_ORG_SETTINGS_REPAIRED_BINDS: set[int] = set()
_ORGANIZATION_REPAIRED_BINDS: set[int] = set()
_ORGANIZATION_REPAIR_LOCK = Lock()


ORGANIZATION_COLUMNS: dict[str, str] = {
    "trial_started_at": "DATETIME NULL",
    "trial_ends_at": "DATETIME NULL",
    "pilot_code": "VARCHAR(80) NOT NULL DEFAULT ''",
}


ORG_SETTINGS_COLUMNS: dict[str, str] = {
    "smtp_host": "VARCHAR(255) NOT NULL DEFAULT ''",
    "smtp_port": "INTEGER NOT NULL DEFAULT 587",
    "smtp_username": "VARCHAR(255) NOT NULL DEFAULT ''",
    "smtp_password": "VARCHAR(255) NOT NULL DEFAULT ''",
    "smtp_use_tls": "BOOLEAN NOT NULL DEFAULT 1",
    "smtp_from_email": "VARCHAR(255) NOT NULL DEFAULT ''",
    "imap_host": "VARCHAR(255) NOT NULL DEFAULT 'imap.gmail.com'",
    "imap_port": "INTEGER NOT NULL DEFAULT 993",
    "imap_username": "VARCHAR(255) NOT NULL DEFAULT ''",
    "imap_password": "VARCHAR(255) NOT NULL DEFAULT ''",
    "imap_mailbox": "VARCHAR(100) NOT NULL DEFAULT 'INBOX'",
    "imap_search_criteria": "VARCHAR(100) NOT NULL DEFAULT 'UNSEEN'",
    "inbox_poll_enabled": "BOOLEAN NOT NULL DEFAULT 0",
    "business_name": "VARCHAR(255) NOT NULL DEFAULT ''",
    "business_services": "TEXT NULL",
    "business_area": "VARCHAR(255) NOT NULL DEFAULT ''",
    "business_hours": "VARCHAR(255) NOT NULL DEFAULT 'Mon-Fri 8am-5pm'",
    "business_phone": "VARCHAR(100) NOT NULL DEFAULT ''",
    "business_tone": "VARCHAR(255) NOT NULL DEFAULT 'friendly and professional'",
    "business_reply_signature": "TEXT NULL",
    "twilio_account_sid": "VARCHAR(100) NOT NULL DEFAULT ''",
    "twilio_auth_token": "VARCHAR(100) NOT NULL DEFAULT ''",
    "twilio_from_number": "VARCHAR(50) NOT NULL DEFAULT ''",
    "sms_alert_to_number": "VARCHAR(50) NOT NULL DEFAULT ''",
    "google_oauth_access_token": "TEXT NULL",
    "google_oauth_refresh_token": "TEXT NULL",
    "google_oauth_token_expires_at": "DATETIME NULL",
    "google_oauth_email": "VARCHAR(255) NOT NULL DEFAULT ''",
    "scheduling_enabled": "BOOLEAN NOT NULL DEFAULT 0",
    "scheduling_slot_duration": "INTEGER NOT NULL DEFAULT 60",
    "scheduling_buffer_minutes": "INTEGER NOT NULL DEFAULT 0",
    "scheduling_max_days_ahead": "INTEGER NOT NULL DEFAULT 7",
    "google_calendar_id": "VARCHAR(255) NOT NULL DEFAULT 'primary'",
    "google_calendar_sync_enabled": "BOOLEAN NOT NULL DEFAULT 0",
    "review_request_enabled": "BOOLEAN NOT NULL DEFAULT 0",
    "review_url": "VARCHAR(500) NOT NULL DEFAULT ''",
    "review_delay_hours": "INTEGER NOT NULL DEFAULT 72",
    "review_request_channel": "VARCHAR(20) NOT NULL DEFAULT 'email'",
    "review_request_subject": "VARCHAR(255) NOT NULL DEFAULT 'Quick favor - would you mind leaving us a review?'",
    "review_request_body": "TEXT NULL",
    "outbound_webhook_enabled": "BOOLEAN NOT NULL DEFAULT 0",
    "outbound_webhook_url": "VARCHAR(500) NOT NULL DEFAULT ''",
    "outbound_webhook_secret": "VARCHAR(255) NOT NULL DEFAULT ''",
    "outbound_webhook_events": "VARCHAR(255) NOT NULL DEFAULT 'lead.created,booking.created,lead.won'",
    "human_review": "BOOLEAN NOT NULL DEFAULT 1",
    "automation_paused": "BOOLEAN NOT NULL DEFAULT 0",
    "auto_send_confidence_threshold": "FLOAT NOT NULL DEFAULT 0.85",
    "forwarding_token": "VARCHAR(100) NOT NULL DEFAULT ''",
    "owner_alert_email": "VARCHAR(255) NOT NULL DEFAULT ''",
    "digest_to_email": "VARCHAR(255) NOT NULL DEFAULT ''",
    "default_timezone": "VARCHAR(100) NOT NULL DEFAULT 'America/Edmonton'",
}


def _is_duplicate_column_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "duplicate column" in message or "already exists" in message


def ensure_org_settings_schema(db: Session) -> None:
    """Add org_settings columns that can be missing on older cPanel databases."""
    bind = db.get_bind()
    bind_key = id(bind)
    if bind_key in _ORG_SETTINGS_REPAIRED_BINDS:
        return

    inspector = inspect(bind)
    if "org_settings" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("org_settings")}
    missing = [(name, ddl) for name, ddl in ORG_SETTINGS_COLUMNS.items() if name not in existing]
    if not missing:
        _ORG_SETTINGS_REPAIRED_BINDS.add(bind_key)
        return

    for name, ddl in missing:
        try:
            db.execute(text(f"ALTER TABLE org_settings ADD COLUMN {name} {ddl}"))
        except (OperationalError, ProgrammingError) as exc:
            db.rollback()
            if not _is_duplicate_column_error(exc):
                raise
        else:
            logger.warning("Added missing org_settings.%s column", name)

    db.commit()
    _ORG_SETTINGS_REPAIRED_BINDS.add(bind_key)


def ensure_organization_schema(db: Session) -> None:
    """Add organization columns that can be missing on older cPanel databases."""
    bind = db.get_bind()
    bind_key = id(bind)
    if bind_key in _ORGANIZATION_REPAIRED_BINDS:
        return

    with _ORGANIZATION_REPAIR_LOCK:
        if bind_key in _ORGANIZATION_REPAIRED_BINDS:
            return

        inspector = inspect(bind)
        if "organizations" not in inspector.get_table_names():
            _ORGANIZATION_REPAIRED_BINDS.add(bind_key)
            return

        existing = {column["name"] for column in inspector.get_columns("organizations")}
        missing = [(name, ddl) for name, ddl in ORGANIZATION_COLUMNS.items() if name not in existing]
        if not missing:
            _ORGANIZATION_REPAIRED_BINDS.add(bind_key)
            return

        for name, ddl in missing:
            try:
                db.execute(text(f"ALTER TABLE organizations ADD COLUMN {name} {ddl}"))
            except (OperationalError, ProgrammingError) as exc:
                db.rollback()
                if not _is_duplicate_column_error(exc):
                    raise
            else:
                logger.warning("Added missing organizations.%s column", name)

        db.commit()
        _ORGANIZATION_REPAIRED_BINDS.add(bind_key)
