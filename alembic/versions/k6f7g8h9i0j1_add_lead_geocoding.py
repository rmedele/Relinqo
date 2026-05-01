"""add lead geocoding

Revision ID: k6f7g8h9i0j1
Revises: j5e6f7g8h9i0
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k6f7g8h9i0j1"
down_revision: Union[str, None] = "j5e6f7g8h9i0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        if not _has_column("leads", "latitude"):
            batch_op.add_column(sa.Column("latitude", sa.Float(), nullable=True))
        if not _has_column("leads", "longitude"):
            batch_op.add_column(sa.Column("longitude", sa.Float(), nullable=True))
        if not _has_column("leads", "geocoded_location"):
            batch_op.add_column(sa.Column("geocoded_location", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        if _has_column("leads", "geocoded_location"):
            batch_op.drop_column("geocoded_location")
        if _has_column("leads", "longitude"):
            batch_op.drop_column("longitude")
        if _has_column("leads", "latitude"):
            batch_op.drop_column("latitude")
