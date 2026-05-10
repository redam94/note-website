"""add note_visibility_changes audit table

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-04-19 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'note_visibility_changes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('note_id', sa.Integer(), nullable=False),
        sa.Column('from_visibility', sa.Text(), nullable=False),
        sa.Column('to_visibility', sa.Text(), nullable=False),
        sa.Column('actor', sa.Text(), nullable=False),
        sa.Column('changed_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['note_id'], ['notes.id'], ondelete='CASCADE'),
    )
    op.create_index(
        'ix_note_visibility_changes_note_id',
        'note_visibility_changes',
        ['note_id'],
    )
    op.create_index(
        'ix_note_visibility_changes_changed_at',
        'note_visibility_changes',
        ['changed_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_note_visibility_changes_changed_at', table_name='note_visibility_changes')
    op.drop_index('ix_note_visibility_changes_note_id', table_name='note_visibility_changes')
    op.drop_table('note_visibility_changes')
