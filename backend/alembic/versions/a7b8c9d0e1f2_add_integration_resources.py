"""add integration_resources table

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-19 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'integration_resources',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('resource_type', sa.Text(), nullable=False),
        sa.Column('external_id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('last_synced_at', sa.Text(), nullable=True),
        sa.Column('sync_cursor', sa.Text(), nullable=True),
        sa.Column('config_json', sa.Text(), nullable=True, server_default='{}'),
        sa.Column('space_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['account_id'], ['connected_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'], ondelete='SET NULL'),
    )
    op.create_index(
        'ix_integration_resources_account_type_ext',
        'integration_resources',
        ['account_id', 'resource_type', 'external_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('ix_integration_resources_account_type_ext', table_name='integration_resources')
    op.drop_table('integration_resources')
