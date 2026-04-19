"""add connected_accounts table and notes.visibility column

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-19 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = '56cb451a00e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'connected_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('integration_type', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('external_account_id', sa.Text(), nullable=True),
        sa.Column('oauth_access_token_enc', sa.Text(), nullable=True),
        sa.Column('oauth_refresh_token_enc', sa.Text(), nullable=True),
        sa.Column('token_expires_at', sa.Text(), nullable=True),
        sa.Column('scopes', sa.Text(), nullable=True, server_default='[]'),
        sa.Column('metadata_json', sa.Text(), nullable=True, server_default='{}'),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_connected_accounts_type_ext',
        'connected_accounts',
        ['integration_type', 'external_account_id'],
        unique=False,
    )

    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'visibility',
                sa.Text(),
                nullable=False,
                server_default='public',
            )
        )
    op.execute("UPDATE notes SET visibility = 'public' WHERE visibility IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_column('visibility')

    op.drop_index('ix_connected_accounts_type_ext', table_name='connected_accounts')
    op.drop_table('connected_accounts')
