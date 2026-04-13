"""add spaces table and space_id to all tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-13 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create spaces table
    op.create_table('spaces',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug')
    )

    # 2. Insert default space
    op.execute(
        "INSERT INTO spaces (id, name, slug, description, created_at) "
        "VALUES (1, 'Default', 'default', 'Default space', datetime('now'))"
    )

    # 3. Add space_id to notes
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('space_id', sa.Integer(), nullable=True))
    op.execute("UPDATE notes SET space_id = 1")
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.alter_column('space_id', nullable=False, server_default='1')
        batch_op.create_foreign_key('fk_notes_space_id', 'spaces', ['space_id'], ['id'], ondelete='CASCADE')

    # 4. Add space_id to documents
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('space_id', sa.Integer(), nullable=True))
    op.execute("UPDATE documents SET space_id = 1")
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.alter_column('space_id', nullable=False, server_default='1')
        batch_op.create_foreign_key('fk_documents_space_id', 'spaces', ['space_id'], ['id'], ondelete='CASCADE')

    # 5. Add space_id to subgraph_nodes
    with op.batch_alter_table('subgraph_nodes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('space_id', sa.Integer(), nullable=True))
    op.execute("UPDATE subgraph_nodes SET space_id = 1")
    with op.batch_alter_table('subgraph_nodes', schema=None) as batch_op:
        batch_op.alter_column('space_id', nullable=False, server_default='1')
        batch_op.create_foreign_key('fk_subgraph_nodes_space_id', 'spaces', ['space_id'], ['id'], ondelete='CASCADE')

    # 6. Add space_id to subgraph_edges
    with op.batch_alter_table('subgraph_edges', schema=None) as batch_op:
        batch_op.add_column(sa.Column('space_id', sa.Integer(), nullable=True))
    op.execute("UPDATE subgraph_edges SET space_id = 1")
    with op.batch_alter_table('subgraph_edges', schema=None) as batch_op:
        batch_op.alter_column('space_id', nullable=False, server_default='1')
        batch_op.create_foreign_key('fk_subgraph_edges_space_id', 'spaces', ['space_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    with op.batch_alter_table('subgraph_edges', schema=None) as batch_op:
        batch_op.drop_constraint('fk_subgraph_edges_space_id', type_='foreignkey')
        batch_op.drop_column('space_id')

    with op.batch_alter_table('subgraph_nodes', schema=None) as batch_op:
        batch_op.drop_constraint('fk_subgraph_nodes_space_id', type_='foreignkey')
        batch_op.drop_column('space_id')

    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_documents_space_id', type_='foreignkey')
        batch_op.drop_column('space_id')

    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_constraint('fk_notes_space_id', type_='foreignkey')
        batch_op.drop_column('space_id')

    op.drop_table('spaces')
