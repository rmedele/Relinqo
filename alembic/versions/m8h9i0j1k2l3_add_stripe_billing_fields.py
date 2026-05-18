"""add stripe billing fields

Revision ID: m8h9i0j1k2l3
Revises: l7g8h9i0j1k2
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m8h9i0j1k2l3"
down_revision: Union[str, None] = "l7g8h9i0j1k2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch_op:
        if not _has_column("organizations", "stripe_customer_id"):
            batch_op.add_column(sa.Column("stripe_customer_id", sa.String(length=100), nullable=True))
        if not _has_column("organizations", "stripe_subscription_id"):
            batch_op.add_column(sa.Column("stripe_subscription_id", sa.String(length=100), nullable=True))
        if not _has_column("organizations", "subscription_current_period_end"):
            batch_op.add_column(sa.Column("subscription_current_period_end", sa.DateTime(timezone=True), nullable=True))
        if not _has_column("organizations", "subscription_cancel_at_period_end"):
            batch_op.add_column(sa.Column("subscription_cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()))
        if not _has_column("organizations", "billing_exempt"):
            batch_op.add_column(sa.Column("billing_exempt", sa.Boolean(), nullable=False, server_default=sa.false()))
        if not _has_column("organizations", "billing_exempt_reason"):
            batch_op.add_column(sa.Column("billing_exempt_reason", sa.String(length=255), nullable=False, server_default=""))

    indexes = _index_names("organizations")
    if "ix_organizations_stripe_customer_id" not in indexes:
        op.create_index("ix_organizations_stripe_customer_id", "organizations", ["stripe_customer_id"], unique=True)
    if "ix_organizations_stripe_subscription_id" not in indexes:
        op.create_index("ix_organizations_stripe_subscription_id", "organizations", ["stripe_subscription_id"], unique=True)
    if "ix_organizations_billing_exempt" not in indexes:
        op.create_index("ix_organizations_billing_exempt", "organizations", ["billing_exempt"])


def downgrade() -> None:
    indexes = _index_names("organizations")
    if "ix_organizations_billing_exempt" in indexes:
        op.drop_index("ix_organizations_billing_exempt", table_name="organizations")
    if "ix_organizations_stripe_subscription_id" in indexes:
        op.drop_index("ix_organizations_stripe_subscription_id", table_name="organizations")
    if "ix_organizations_stripe_customer_id" in indexes:
        op.drop_index("ix_organizations_stripe_customer_id", table_name="organizations")

    with op.batch_alter_table("organizations") as batch_op:
        if _has_column("organizations", "billing_exempt_reason"):
            batch_op.drop_column("billing_exempt_reason")
        if _has_column("organizations", "billing_exempt"):
            batch_op.drop_column("billing_exempt")
        if _has_column("organizations", "subscription_cancel_at_period_end"):
            batch_op.drop_column("subscription_cancel_at_period_end")
        if _has_column("organizations", "subscription_current_period_end"):
            batch_op.drop_column("subscription_current_period_end")
        if _has_column("organizations", "stripe_subscription_id"):
            batch_op.drop_column("stripe_subscription_id")
        if _has_column("organizations", "stripe_customer_id"):
            batch_op.drop_column("stripe_customer_id")
