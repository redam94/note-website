"""add_indexes_drop_unused_columns

Revision ID: 56cb451a00e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-15 22:57:06.363600

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '56cb451a00e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('graph_edges', schema=None) as batch_op:
        batch_op.create_index('ix_graph_edges_source_id', ['source_id'], unique=False)
        batch_op.create_index('ix_graph_edges_target_id', ['target_id'], unique=False)
        batch_op.drop_column('reference_count')

    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.create_index('ix_notes_document_id', ['document_id'], unique=False)
        batch_op.create_index('ix_notes_parent_id', ['parent_id'], unique=False)
        batch_op.create_index('ix_notes_space_id', ['space_id'], unique=False)
        batch_op.drop_column('embedding')


def downgrade() -> None:
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('embedding', sa.TEXT(), nullable=True))
        batch_op.drop_index('ix_notes_space_id')
        batch_op.drop_index('ix_notes_parent_id')
        batch_op.drop_index('ix_notes_document_id')

    with op.batch_alter_table('graph_edges', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reference_count', sa.INTEGER(),
                                      server_default=sa.text("'1'"), nullable=False))
        batch_op.drop_index('ix_graph_edges_target_id')
        batch_op.drop_index('ix_graph_edges_source_id')
