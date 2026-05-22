"""add outbound webhook settings

Revision ID: n9i0j1k2l3m4
Revises: m8h9i0j1k2l3
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n9i0j1k2l3m4"
down_revision: Union[str, None] = "m8h9i0j1k2l3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch_op:
        if not _has_column("org_settings", "outbound_webhook_enabled"):
            batch_op.add_column(sa.Column("outbound_webhook_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        if not _has_column("org_settings", "outbound_webhook_url"):
            batch_op.add_column(sa.Column("outbound_webhook_url", sa.String(length=500), nullable=False, server_default=""))
        if not _has_column("org_settings", "outbound_webhook_secret"):
            batch_op.add_column(sa.Column("outbound_webhook_secret", sa.String(length=255), nullable=False, server_default=""))
        if not _has_column("org_settings", "outbound_webhook_events"):
            batch_op.add_column(sa.Column("outbound_webhook_events", sa.String(length=255), nullable=False, server_default="lead.created,booking.created,lead.won"))


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch_op:
        if _has_column("org_settings", "outbound_webhook_events"):
            batch_op.drop_column("outbound_webhook_events")
        if _has_column("org_settings", "outbound_webhook_secret"):
            batch_op.drop_column("outbound_webhook_secret")
        if _has_column("org_settings", "outbound_webhook_url"):
            batch_op.drop_column("outbound_webhook_url")
        if _has_column("org_settings", "outbound_webhook_enabled"):
            batch_op.drop_column("outbound_webhook_enabled")
