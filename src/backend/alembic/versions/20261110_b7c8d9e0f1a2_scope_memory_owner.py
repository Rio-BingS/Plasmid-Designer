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


_PK_NAME = 'sequencing_scope_memory_pkey'


def upgrade() -> None:
    # 单列主键 -> (owner_id, reference_hash) 复合主键。四步都增量：
    # 加可空列 → 回填匿名 owner → 删旧主键 → 收窄非空并建复合主键。
    #
    # 主键必须显式 drop/create：batch_alter_table 在 PostgreSQL 上走原生
    # ALTER，`copy_from` 里的主键只在 SQLite 重建表时才有意义——早先只在
    # batch 里 alter_column 的话，列加上了、主键仍是单列，两个用户对同一
    # 质粒的记忆会互相覆盖（SM1 失效）。
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
        # SQLite 分支：重建表时 copy_from 已给出复合主键，这里删旧名会报
        # 找不到约束——按方言分支，别让一次迁移在两种库里行为不同
        if op.get_context().dialect.name != 'sqlite':
            batch.drop_constraint(_PK_NAME, type_='primary')
            batch.create_primary_key(_PK_NAME, ['owner_id', 'reference_hash'])


def downgrade() -> None:
    # 复合主键收回单列。顺序：去重 → 删复合主键（仅 PG）→ 删列。
    # 去重必须在前：旧结构一个 reference_hash 只能留一行，同参考有多用户
    # 记忆时保留 updated_at 最新的一条（属预期损失，记在迁移说明里）。
    # 在 Python 侧选keep键再逐条删——`rowid` 只有 SQLite 有，不能写进通用谓词。
    is_pg = op.get_context().dialect.name != 'sqlite'
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT owner_id, reference_hash, updated_at FROM sequencing_scope_memory"
    )).fetchall()
    keeper: dict = {}
    for owner_id, ref_hash, updated in rows:
        cur = keeper.get(ref_hash)
        # updated_at 是 datetime（可能为 NULL）——不能和字符串混比
        if cur is None or (updated is not None
                           and (cur[1] is None or updated > cur[1])):
            keeper[ref_hash] = (owner_id, updated)
    keep_pairs = {(oid, h) for h, (oid, _u) in keeper.items()}
    for owner_id, ref_hash, _updated in rows:
        if (owner_id, ref_hash) not in keep_pairs:
            conn.execute(sa.text(
                "DELETE FROM sequencing_scope_memory "
                "WHERE reference_hash = :h AND owner_id = :o").bindparams(
                    h=ref_hash, o=owner_id))
    if is_pg:
        op.drop_constraint(_PK_NAME, 'sequencing_scope_memory', type_='primary')
    with op.batch_alter_table(
            'sequencing_scope_memory',
            copy_from=_table('reference_hash'),
    ) as batch:
        # 非 PG：重建表时 copy_from 已按单列主键收口，只需放掉非空约束
        batch.alter_column('owner_id',
                           existing_type=sa.String(length=64), nullable=True)
    op.drop_column('sequencing_scope_memory', 'owner_id')
