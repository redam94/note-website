"""add hierarchy to subgraph nodes

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-13 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('subgraph_nodes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent_cluster_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('path', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('level', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('subgraph_nodes', schema=None) as batch_op:
        batch_op.drop_column('level')
        batch_op.drop_column('path')
        batch_op.drop_column('parent_cluster_id')
