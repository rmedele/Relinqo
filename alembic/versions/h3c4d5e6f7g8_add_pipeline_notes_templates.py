"""add pipeline tracking, internal notes, reply templates

Adds:
  leads.deal_value, leads.tags, leads.pipeline_stage, leads.starred, leads.last_contacted_at
  lead_notes table (internal team notes)
  reply_templates table

Revision ID: h3c4d5e6f7g8
Revises: g2b3c4d5e6f7
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h3c4d5e6f7g8'
down_revision: Union[str, Sequence[str], None] = 'g2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table: str, col: str) -> bool:
    return any(c["name"] == col for c in inspector.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # --- Lead column additions (idempotent) ---
    with op.batch_alter_table("leads") as batch:
        if not _has_column(inspector, "leads", "deal_value"):
            batch.add_column(sa.Column("deal_value", sa.Float(), nullable=True))
        if not _has_column(inspector, "leads", "tags"):
            batch.add_column(sa.Column("tags", sa.String(500), nullable=False, server_default=""))
        if not _has_column(inspector, "leads", "pipeline_stage"):
            batch.add_column(sa.Column("pipeline_stage", sa.String(50), nullable=False, server_default="new"))
        if not _has_column(inspector, "leads", "starred"):
            batch.add_column(sa.Column("starred", sa.Boolean(), nullable=False, server_default=sa.false()))
        if not _has_column(inspector, "leads", "last_contacted_at"):
            batch.add_column(sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True))

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("leads")}
    if "ix_leads_pipeline_stage" not in existing_indexes:
        op.create_index("ix_leads_pipeline_stage", "leads", ["pipeline_stage"])
    if "ix_leads_starred" not in existing_indexes:
        op.create_index("ix_leads_starred", "leads", ["starred"])

    # --- lead_notes ---
    if "lead_notes" not in existing_tables:
        op.create_table(
            "lead_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("author_name", sa.String(255), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_lead_notes_org_id", "lead_notes", ["org_id"])
        op.create_index("ix_lead_notes_lead_id", "lead_notes", ["lead_id"])

    # --- reply_templates ---
    if "reply_templates" not in existing_tables:
        op.create_table(
            "reply_templates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("category", sa.String(50), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_reply_templates_org_id", "reply_templates", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_reply_templates_org_id", table_name="reply_templates")
    op.drop_table("reply_templates")
    op.drop_index("ix_lead_notes_lead_id", table_name="lead_notes")
    op.drop_index("ix_lead_notes_org_id", table_name="lead_notes")
    op.drop_table("lead_notes")
    op.drop_index("ix_leads_starred", table_name="leads")
    op.drop_index("ix_leads_pipeline_stage", table_name="leads")
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("last_contacted_at")
        batch.drop_column("starred")
        batch.drop_column("pipeline_stage")
        batch.drop_column("tags")
        batch.drop_column("deal_value")
