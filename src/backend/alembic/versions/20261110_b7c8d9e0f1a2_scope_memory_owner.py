"""scope memory: add owner_id for per-user isolation (v3.1 X5)

范围记忆从「参考指纹」改为「用户 + 参考指纹」复合键。匿名/单用户部署下
owner 取固定值（`__anonymous__`），语义与旧版一致但不再跨用户串味。

SQLite 不支持 ALTER COLUMN / DROP CONSTRAINT 直接改主键，因此走
`batch_alter_table`（SQLite 下由 alembic 重建表并搬数据，PostgreSQL 下仍是
原生 ALTER）——既是增量迁移，也不丢既有记忆。`copy_from` 显式给出目标表
结构，避免 batch 反射旧主键产生的 SAWarning。

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ANON = '__anonymous__'


def _table(*pk_cols: str) -> sa.Table:
    """目标表结构（batch 重建时用；主键列按调用方给定）"""
    meta = sa.MetaData()
    return sa.Table(
        'sequencing_scope_memory', meta,
        sa.Column('owner_id', sa.String(length=64), nullable=False),
        sa.Column('reference_hash', sa.String(length=64), nullable=False),
        sa.Column('scope_json', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint(*pk_cols),
    )


def upgrade() -> None:
    # 先加可空列并回填匿名 owner，再收窄为复合主键——三步都是增量操作
    op.add_column('sequencing_scope_memory',
                  sa.Column('owner_id', sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE sequencing_scope_memory SET owner_id = '%s' "
        "WHERE owner_id IS NULL" % _ANON)
    with op.batch_alter_table(
            'sequencing_scope_memory',
            copy_from=_table('owner_id', 'reference_hash'),
    ) as batch:
        batch.alter_column('owner_id',
                           existing_type=sa.String(length=64), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table(
            'sequencing_scope_memory',
            copy_from=_table('reference_hash'),
    ) as batch:
        batch.drop_constraint('sequencing_scope_memory_pkey', type_='primary')
    op.drop_column('sequencing_scope_memory', 'owner_id')
