"""add phone forwarding setup state

Revision ID: l7g8h9i0j1k2
Revises: k6f7g8h9i0j1
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "l7g8h9i0j1k2"
down_revision: Union[str, None] = "k6f7g8h9i0j1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    with op.batch_alter_table("phone_routing_rules") as batch_op:
        if not _has_column("phone_routing_rules", "current_business_number"):
            batch_op.add_column(sa.Column("current_business_number", sa.String(length=32), nullable=False, server_default=""))
        if not _has_column("phone_routing_rules", "forwarding_carrier"):
            batch_op.add_column(sa.Column("forwarding_carrier", sa.String(length=50), nullable=False, server_default="unknown"))
        if not _has_column("phone_routing_rules", "forwarding_code_used"):
            batch_op.add_column(sa.Column("forwarding_code_used", sa.String(length=100), nullable=False, server_default=""))
        if not _has_column("phone_routing_rules", "forwarding_setup_status"):
            batch_op.add_column(sa.Column("forwarding_setup_status", sa.String(length=30), nullable=False, server_default="not_started"))
        if not _has_column("phone_routing_rules", "forwarding_test_started_at"):
            batch_op.add_column(sa.Column("forwarding_test_started_at", sa.DateTime(timezone=True), nullable=True))
        if not _has_column("phone_routing_rules", "forwarding_test_call_event_id"):
            batch_op.add_column(sa.Column("forwarding_test_call_event_id", sa.Integer(), sa.ForeignKey("call_events.id"), nullable=True))
        if not _has_column("phone_routing_rules", "forwarding_verified_at"):
            batch_op.add_column(sa.Column("forwarding_verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("phone_routing_rules") as batch_op:
        if _has_column("phone_routing_rules", "forwarding_verified_at"):
            batch_op.drop_column("forwarding_verified_at")
        if _has_column("phone_routing_rules", "forwarding_test_call_event_id"):
            batch_op.drop_column("forwarding_test_call_event_id")
        if _has_column("phone_routing_rules", "forwarding_test_started_at"):
            batch_op.drop_column("forwarding_test_started_at")
        if _has_column("phone_routing_rules", "forwarding_setup_status"):
            batch_op.drop_column("forwarding_setup_status")
        if _has_column("phone_routing_rules", "forwarding_code_used"):
            batch_op.drop_column("forwarding_code_used")
        if _has_column("phone_routing_rules", "forwarding_carrier"):
            batch_op.drop_column("forwarding_carrier")
        if _has_column("phone_routing_rules", "current_business_number"):
            batch_op.drop_column("current_business_number")
