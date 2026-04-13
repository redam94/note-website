"""add subgraph tables and reference count

Revision ID: a1b2c3d4e5f6
Revises: f65b0e1d2eda
Create Date: 2026-04-12 23:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f65b0e1d2eda'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add subgraph_nodes, subgraph_edges tables and reference_count/cluster_id columns."""
    # Create subgraph_nodes first (notes.cluster_id references it)
    op.create_table('subgraph_nodes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('member_node_ids', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('subgraph_edges',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source_cluster_id', sa.Integer(), nullable=False),
        sa.Column('target_cluster_id', sa.Integer(), nullable=False),
        sa.Column('weight', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('cross_edge_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['source_cluster_id'], ['subgraph_nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_cluster_id'], ['subgraph_nodes.id'], ondelete='CASCADE'),
    )

    with op.batch_alter_table('graph_edges', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reference_count', sa.Integer(), nullable=False, server_default='1'))

    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cluster_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_notes_cluster_id', 'subgraph_nodes', ['cluster_id'], ['id'],
            ondelete='SET NULL'
        )


def downgrade() -> None:
    """Remove subgraph tables and new columns."""
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_constraint('fk_notes_cluster_id', type_='foreignkey')
        batch_op.drop_column('cluster_id')

    with op.batch_alter_table('graph_edges', schema=None) as batch_op:
        batch_op.drop_column('reference_count')

    op.drop_table('subgraph_edges')
    op.drop_table('subgraph_nodes')
