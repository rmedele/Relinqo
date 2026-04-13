"""add_google_oauth_fields

Revision ID: c69fc6b8b1c2
Revises: d4e5f6a7b8c9
Create Date: 2026-04-13 01:41:14.683884

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c69fc6b8b1c2'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('org_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('google_oauth_access_token', sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column('google_oauth_refresh_token', sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column('google_oauth_token_expires_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('google_oauth_email', sa.String(length=255), nullable=False, server_default=""))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('org_settings', schema=None) as batch_op:
        batch_op.drop_column('google_oauth_email')
        batch_op.drop_column('google_oauth_token_expires_at')
        batch_op.drop_column('google_oauth_refresh_token')
        batch_op.drop_column('google_oauth_access_token')
