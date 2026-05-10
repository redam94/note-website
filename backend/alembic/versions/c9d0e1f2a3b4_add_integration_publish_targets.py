"""add integration_publish_targets table

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-04-19 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'integration_publish_targets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=False),
        sa.Column('resource_id', sa.Integer(), nullable=True),
        sa.Column('repo_full_name', sa.Text(), nullable=False),
        sa.Column('wiki_branch', sa.Text(), nullable=False, server_default='master'),
        sa.Column('last_published_sha', sa.Text(), nullable=True),
        sa.Column('last_published_at', sa.Text(), nullable=True),
        sa.Column('config_json', sa.Text(), nullable=True, server_default='{}'),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['account_id'], ['connected_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['resource_id'], ['integration_resources.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('space_id', name='uq_integration_publish_targets_space'),
    )


def downgrade() -> None:
    op.drop_table('integration_publish_targets')
