"""add extraction profiles table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-15 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extraction_profiles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True, server_default=''),
        sa.Column('extensions', sa.Text(), nullable=True, server_default='[]'),
        sa.Column('mime_types', sa.Text(), nullable=True, server_default='[]'),
        sa.Column('doc_type_override', sa.Text(), nullable=True),
        sa.Column('script', sa.Text(), nullable=True, server_default=''),
        sa.Column('prompt_additions', sa.Text(), nullable=True, server_default=''),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'], ondelete='CASCADE'),
    )


def downgrade() -> None:
    op.drop_table('extraction_profiles')
