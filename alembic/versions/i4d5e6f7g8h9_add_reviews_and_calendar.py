"""add review request automation + Google Calendar sync fields

Adds:
  org_settings.review_request_enabled, review_url, review_delay_hours,
                review_request_channel, review_request_subject, review_request_body
  org_settings.google_calendar_id, google_calendar_sync_enabled
  bookings.google_event_id
  review_requests table

Revision ID: i4d5e6f7g8h9
Revises: h3c4d5e6f7g8
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i4d5e6f7g8h9"
down_revision: Union[str, Sequence[str], None] = "h3c4d5e6f7g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table: str, col: str) -> bool:
    return any(c["name"] == col for c in inspector.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    DEFAULT_REVIEW_BODY = (
        "Hi {{name}},\n\n"
        "Thanks again for choosing {{business}} — it was a pleasure working with you.\n\n"
        "If you have 30 seconds, we'd really appreciate a quick Google review:\n"
        "{{review_url}}\n\n"
        "Reviews from neighbors like you are how small businesses like ours stay busy.\n\n"
        "Thanks,\n{{business}}\n"
    )

    with op.batch_alter_table("org_settings") as batch:
        if not _has_column(inspector, "org_settings", "review_request_enabled"):
            batch.add_column(sa.Column("review_request_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        if not _has_column(inspector, "org_settings", "review_url"):
            batch.add_column(sa.Column("review_url", sa.String(500), nullable=False, server_default=""))
        if not _has_column(inspector, "org_settings", "review_delay_hours"):
            batch.add_column(sa.Column("review_delay_hours", sa.Integer(), nullable=False, server_default="72"))
        if not _has_column(inspector, "org_settings", "review_request_channel"):
            batch.add_column(sa.Column("review_request_channel", sa.String(20), nullable=False, server_default="email"))
        if not _has_column(inspector, "org_settings", "review_request_subject"):
            batch.add_column(sa.Column("review_request_subject", sa.String(255), nullable=False, server_default="Quick favor — would you mind leaving us a review?"))
        if not _has_column(inspector, "org_settings", "review_request_body"):
            batch.add_column(sa.Column("review_request_body", sa.Text(), nullable=False, server_default=DEFAULT_REVIEW_BODY))
        if not _has_column(inspector, "org_settings", "google_calendar_id"):
            batch.add_column(sa.Column("google_calendar_id", sa.String(255), nullable=False, server_default="primary"))
        if not _has_column(inspector, "org_settings", "google_calendar_sync_enabled"):
            batch.add_column(sa.Column("google_calendar_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("bookings") as batch:
        if not _has_column(inspector, "bookings", "google_event_id"):
            batch.add_column(sa.Column("google_event_id", sa.String(255), nullable=True))

    if "review_requests" not in existing_tables:
        op.create_table(
            "review_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id"), nullable=False),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
            sa.Column("channel", sa.String(20), nullable=False, server_default="email"),
            sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_review_requests_org_id", "review_requests", ["org_id"])
        op.create_index("ix_review_requests_lead_id", "review_requests", ["lead_id"])
        op.create_index("ix_review_requests_status", "review_requests", ["status"])
        op.create_index("ix_review_requests_scheduled_for", "review_requests", ["scheduled_for"])


def downgrade() -> None:
    op.drop_index("ix_review_requests_scheduled_for", table_name="review_requests")
    op.drop_index("ix_review_requests_status", table_name="review_requests")
    op.drop_index("ix_review_requests_lead_id", table_name="review_requests")
    op.drop_index("ix_review_requests_org_id", table_name="review_requests")
    op.drop_table("review_requests")

    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("google_event_id")

    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("google_calendar_sync_enabled")
        batch.drop_column("google_calendar_id")
        batch.drop_column("review_request_body")
        batch.drop_column("review_request_subject")
        batch.drop_column("review_request_channel")
        batch.drop_column("review_delay_hours")
        batch.drop_column("review_url")
        batch.drop_column("review_request_enabled")
