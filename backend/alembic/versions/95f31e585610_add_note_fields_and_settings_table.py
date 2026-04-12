"""add note fields and settings table

Revision ID: 95f31e585610
Revises:
Create Date: 2026-04-12 03:05:12.561813

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95f31e585610'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('settings',
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('key')
    )

    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('chapter', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('page', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('summary', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_column('summary')
        batch_op.drop_column('page')
        batch_op.drop_column('chapter')
        batch_op.drop_column('source')

    op.drop_table('settings')
