"""add business knowledge documents

Revision ID: o1p2q3r4s5t6
Revises: n9i0j1k2l3m4
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa


revision = "o1p2q3r4s5t6"
down_revision = "n9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "business_knowledge_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False, server_default="manual"),
        sa.Column("category", sa.String(length=80), nullable=False, server_default="general"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_business_knowledge_documents_id"), "business_knowledge_documents", ["id"], unique=False)
    op.create_index(op.f("ix_business_knowledge_documents_org_id"), "business_knowledge_documents", ["org_id"], unique=False)
    op.create_index(op.f("ix_business_knowledge_documents_is_active"), "business_knowledge_documents", ["is_active"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_business_knowledge_documents_is_active"), table_name="business_knowledge_documents")
    op.drop_index(op.f("ix_business_knowledge_documents_org_id"), table_name="business_knowledge_documents")
    op.drop_index(op.f("ix_business_knowledge_documents_id"), table_name="business_knowledge_documents")
    op.drop_table("business_knowledge_documents")
