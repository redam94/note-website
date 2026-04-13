"""add checkpoint columns to documents

Revision ID: f65b0e1d2eda
Revises: 205d432c296d
Create Date: 2026-04-12 21:33:47.775863
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f65b0e1d2eda'
down_revision: Union[str, Sequence[str], None] = '205d432c296d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('checkpoint', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('last_completed_node', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('last_completed_node')
        batch_op.drop_column('checkpoint')
