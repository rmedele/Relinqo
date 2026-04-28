"""add launch controls and api key hashes

Revision ID: j5e6f7g8h9i0
Revises: i4d5e6f7g8h9
Create Date: 2026-04-28
"""
from typing import Sequence, Union
import hashlib

from alembic import op
import sqlalchemy as sa


revision: str = "j5e6f7g8h9i0"
down_revision: Union[str, Sequence[str], None] = "i4d5e6f7g8h9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table: str, col: str) -> bool:
    return any(c["name"] == col for c in inspector.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    with op.batch_alter_table("organizations") as batch:
        if not _has_column(inspector, "organizations", "api_key_hash"):
            batch.add_column(sa.Column("api_key_hash", sa.String(64), nullable=True))
        if not _has_column(inspector, "organizations", "subscription_status"):
            batch.add_column(sa.Column("subscription_status", sa.String(20), nullable=False, server_default="trialing"))
        if not _has_column(inspector, "organizations", "plan"):
            batch.add_column(sa.Column("plan", sa.String(50), nullable=False, server_default="beta"))

    inspector = sa.inspect(conn)
    indexes = {idx["name"] for idx in inspector.get_indexes("organizations")}
    if "ix_organizations_api_key_hash" not in indexes:
        op.create_index("ix_organizations_api_key_hash", "organizations", ["api_key_hash"], unique=True)
    if "ix_organizations_subscription_status" not in indexes:
        op.create_index("ix_organizations_subscription_status", "organizations", ["subscription_status"])

    orgs = conn.execute(sa.text("SELECT id, api_key, api_key_hash FROM organizations")).mappings().all()
    for org in orgs:
        if org["api_key"] and not org["api_key_hash"]:
            digest = hashlib.sha256(org["api_key"].encode("utf-8")).hexdigest()
            placeholder = f"deprecated-{org['id']}-{digest[:16]}"
            conn.execute(
                sa.text("UPDATE organizations SET api_key_hash=:digest, api_key=:placeholder WHERE id=:id"),
                {"digest": digest, "placeholder": placeholder, "id": org["id"]},
            )

    with op.batch_alter_table("org_settings") as batch:
        if not _has_column(inspector, "org_settings", "automation_paused"):
            batch.add_column(sa.Column("automation_paused", sa.Boolean(), nullable=False, server_default=sa.false()))

    if "sms_opt_outs" not in tables:
        op.create_table(
            "sms_opt_outs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("phone_number", sa.String(32), nullable=False),
            sa.Column("opted_out_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("opted_in_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source", sa.String(50), nullable=False, server_default="sms"),
        )
        op.create_index("ix_sms_opt_outs_org_id", "sms_opt_outs", ["org_id"])
        op.create_index("ix_sms_opt_outs_phone_number", "sms_opt_outs", ["phone_number"])


def downgrade() -> None:
    op.drop_index("ix_sms_opt_outs_phone_number", table_name="sms_opt_outs")
    op.drop_index("ix_sms_opt_outs_org_id", table_name="sms_opt_outs")
    op.drop_table("sms_opt_outs")

    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("automation_paused")

    op.drop_index("ix_organizations_subscription_status", table_name="organizations")
    op.drop_index("ix_organizations_api_key_hash", table_name="organizations")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("plan")
        batch.drop_column("subscription_status")
        batch.drop_column("api_key_hash")
