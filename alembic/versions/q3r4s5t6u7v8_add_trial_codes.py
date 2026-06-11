"""add trial codes

Revision ID: q3r4s5t6u7v8
Revises: n9i0j1k2l3m4
Create Date: 2026-06-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "q3r4s5t6u7v8"
down_revision: Union[str, None] = "n9i0j1k2l3m4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        if not _has_column("organizations", "trial_started_at"):
            batch.add_column(sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True))
        if not _has_column("organizations", "trial_ends_at"):
            batch.add_column(sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
        if not _has_column("organizations", "pilot_code"):
            batch.add_column(sa.Column("pilot_code", sa.String(length=80), nullable=False, server_default=""))

    indexes = _index_names("organizations")
    if "ix_organizations_trial_ends_at" not in indexes:
        op.create_index("ix_organizations_trial_ends_at", "organizations", ["trial_ends_at"], unique=False)
    if "ix_organizations_pilot_code" not in indexes:
        op.create_index("ix_organizations_pilot_code", "organizations", ["pilot_code"], unique=False)

    if not _has_table("trial_codes"):
        op.create_table(
            "trial_codes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=80), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("max_redemptions", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("redemption_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("trial_days", sa.Integer(), nullable=False, server_default="14"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("source", sa.String(length=80), nullable=False, server_default="manual"),
            sa.Column("redeemed_by_user_id", sa.Integer(), nullable=True),
            sa.Column("redeemed_by_workspace_id", sa.Integer(), nullable=True),
            sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["redeemed_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["redeemed_by_workspace_id"], ["organizations.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_trial_codes_id"), "trial_codes", ["id"], unique=False)
        op.create_index(op.f("ix_trial_codes_code"), "trial_codes", ["code"], unique=True)
        op.create_index(op.f("ix_trial_codes_active"), "trial_codes", ["active"], unique=False)
        op.create_index(op.f("ix_trial_codes_expires_at"), "trial_codes", ["expires_at"], unique=False)
        op.create_index(op.f("ix_trial_codes_redeemed_by_user_id"), "trial_codes", ["redeemed_by_user_id"], unique=False)
        op.create_index(op.f("ix_trial_codes_redeemed_by_workspace_id"), "trial_codes", ["redeemed_by_workspace_id"], unique=False)

    if not _has_table("trial_code_redemptions"):
        op.create_table(
            "trial_code_redemptions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("trial_code_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=80), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.Integer(), nullable=False),
            sa.Column("trial_days", sa.Integer(), nullable=False, server_default="14"),
            sa.Column("redeemed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["trial_code_id"], ["trial_codes.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("trial_code_id", "org_id", name="uq_trial_code_redemptions_code_org"),
        )
        op.create_index(op.f("ix_trial_code_redemptions_id"), "trial_code_redemptions", ["id"], unique=False)
        op.create_index(op.f("ix_trial_code_redemptions_trial_code_id"), "trial_code_redemptions", ["trial_code_id"], unique=False)
        op.create_index(op.f("ix_trial_code_redemptions_code"), "trial_code_redemptions", ["code"], unique=False)
        op.create_index(op.f("ix_trial_code_redemptions_user_id"), "trial_code_redemptions", ["user_id"], unique=False)
        op.create_index(op.f("ix_trial_code_redemptions_org_id"), "trial_code_redemptions", ["org_id"], unique=False)
        op.create_index(op.f("ix_trial_code_redemptions_redeemed_at"), "trial_code_redemptions", ["redeemed_at"], unique=False)


def downgrade() -> None:
    if _has_table("trial_code_redemptions"):
        op.drop_index(op.f("ix_trial_code_redemptions_redeemed_at"), table_name="trial_code_redemptions")
        op.drop_index(op.f("ix_trial_code_redemptions_org_id"), table_name="trial_code_redemptions")
        op.drop_index(op.f("ix_trial_code_redemptions_user_id"), table_name="trial_code_redemptions")
        op.drop_index(op.f("ix_trial_code_redemptions_code"), table_name="trial_code_redemptions")
        op.drop_index(op.f("ix_trial_code_redemptions_trial_code_id"), table_name="trial_code_redemptions")
        op.drop_index(op.f("ix_trial_code_redemptions_id"), table_name="trial_code_redemptions")
        op.drop_table("trial_code_redemptions")

    if _has_table("trial_codes"):
        op.drop_index(op.f("ix_trial_codes_redeemed_by_workspace_id"), table_name="trial_codes")
        op.drop_index(op.f("ix_trial_codes_redeemed_by_user_id"), table_name="trial_codes")
        op.drop_index(op.f("ix_trial_codes_expires_at"), table_name="trial_codes")
        op.drop_index(op.f("ix_trial_codes_active"), table_name="trial_codes")
        op.drop_index(op.f("ix_trial_codes_code"), table_name="trial_codes")
        op.drop_index(op.f("ix_trial_codes_id"), table_name="trial_codes")
        op.drop_table("trial_codes")

    indexes = _index_names("organizations")
    if "ix_organizations_pilot_code" in indexes:
        op.drop_index("ix_organizations_pilot_code", table_name="organizations")
    if "ix_organizations_trial_ends_at" in indexes:
        op.drop_index("ix_organizations_trial_ends_at", table_name="organizations")

    with op.batch_alter_table("organizations") as batch:
        if _has_column("organizations", "pilot_code"):
            batch.drop_column("pilot_code")
        if _has_column("organizations", "trial_ends_at"):
            batch.drop_column("trial_ends_at")
        if _has_column("organizations", "trial_started_at"):
            batch.drop_column("trial_started_at")
