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


_TABLE = 'sequencing_scope_memory'
_FALLBACK_PK_NAME = 'sequencing_scope_memory_pkey'


def _pk_name(conn) -> str:
    """现有主键约束的真实名字。

    不能硬编码 `sequencing_scope_memory_pkey`：PostgreSQL 建主键时若同名约束
    已被占用（多次 upgrade/downgrade 循环会留下名字），会自动改成 `..._pkey1`，
    再按原名 drop 就是 UndefinedObject。这里按目录查真名。
    """
    # 用 CAST(...) 而非 `:t::regclass`——SQLAlchemy 的 text() 会把 `::`
    # 当成参数名的一部分（KeyError: 't'）
    row = conn.execute(sa.text(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = CAST(:t AS regclass) AND contype = 'p'"
    ).bindparams(t=_TABLE)).fetchone()
    return row[0] if row else _FALLBACK_PK_NAME


def upgrade() -> None:
    # 单列主键 -> (owner_id, reference_hash) 复合主键。四步都增量：
    # 加可空列 → 回填匿名 owner → 删旧主键 → 收窄非空并建复合主键。
    #
    # 主键必须显式 drop/create：batch_alter_table 在 PostgreSQL 上走原生
    # ALTER，`copy_from` 里的主键只在 SQLite 重建表时才有意义——早先只在
    # batch 里 alter_column 的话，列加上了、主键仍是单列，两个用户对同一
    # 质粒的记忆会互相覆盖（SM1 失效）。
    op.add_column(_TABLE, sa.Column('owner_id', sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE %s SET owner_id = '%s' WHERE owner_id IS NULL" % (_TABLE, _ANON))
    is_pg = op.get_context().dialect.name != 'sqlite'
    old_pk = _pk_name(op.get_bind()) if is_pg else None
    with op.batch_alter_table(
            _TABLE,
            copy_from=_table('owner_id', 'reference_hash'),
    ) as batch:
        batch.alter_column('owner_id',
                           existing_type=sa.String(length=64), nullable=False)
        # SQLite 分支：重建表时 copy_from 已给出复合主键，不能再删→建，
        # 否则一次迁移在两种库里行为不同
        if is_pg:
            if old_pk:
                batch.drop_constraint(old_pk, type_='primary')
            batch.create_primary_key('sequencing_scope_memory_pkey',
                                     ['owner_id', 'reference_hash'])


def downgrade() -> None:
    # 复合主键收回单列。顺序：去重 → 放非空 → 换主键 → 删列。
    # 去重必须在前：旧结构一个 reference_hash 只能留一行，同参考有多用户
    # 记忆时保留 updated_at 最新的一条（属预期损失，记在迁移说明里）。
    # 在 Python 侧选 keep 键再逐条删——`rowid` 只有 SQLite 有，不能进通用谓词。
    is_pg = op.get_context().dialect.name != 'sqlite'
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT owner_id, reference_hash, updated_at FROM " + _TABLE)).fetchall()
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
                "DELETE FROM " + _TABLE + " "
                "WHERE reference_hash = :h AND owner_id = :o").bindparams(
                    h=ref_hash, o=owner_id))
    # 主键必须显式换回来，且**在 batch 之外**逐条执行：PG 上
    # batch_alter_table 一次性提交，batch 内的 drop/create 与随后的
    # drop_column 顺序不受控（实测报 "column owner_id is in a primary key"）。
    # 顺序：删复合主键 → 放非空 → 删列 → 建单列主键。
    old_pk = _pk_name(conn) if is_pg else None
    with op.batch_alter_table(
            _TABLE,
            copy_from=_table('reference_hash'),
    ) as batch:
        batch.alter_column('owner_id',
                           existing_type=sa.String(length=64), nullable=True)
    if is_pg and old_pk:
        op.drop_constraint(old_pk, _TABLE, type_='primary')
    op.drop_column(_TABLE, 'owner_id')
    if is_pg:
        op.create_primary_key('sequencing_scope_memory_pkey', _TABLE,
                              ['reference_hash'])
