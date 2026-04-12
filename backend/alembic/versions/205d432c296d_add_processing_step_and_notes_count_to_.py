"""add processing_step and notes_count to documents

Revision ID: 205d432c296d
Revises: 95f31e585610
Create Date: 2026-04-12 12:20:24.920527

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '205d432c296d'
down_revision: Union[str, Sequence[str], None] = '95f31e585610'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('processing_step', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('notes_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('notes_count')
        batch_op.drop_column('processing_step')
