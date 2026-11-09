"""sequencing scope memory (W11: reference fingerprint -> last used scope)

Revision ID: a1b2c3d4e5f6
Revises: 35a49b81767c
Create Date: 2026-11-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '35a49b81767c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('sequencing_scope_memory',
    sa.Column('reference_hash', sa.String(length=64), nullable=False),
    sa.Column('scope_json', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('reference_hash')
    )


def downgrade() -> None:
    op.drop_table('sequencing_scope_memory')
