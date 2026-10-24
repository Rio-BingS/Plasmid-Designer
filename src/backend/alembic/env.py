"""Alembic 迁移环境 — 数据库地址与元数据都取自应用自身的单一来源

URL 优先级：-x db_url= > 环境变量 ALEMBIC_DATABASE_URL > app.config settings。
target_metadata 指向 app.database.models.Base.metadata，autogenerate 才能对比
模型与库结构。compare_type 开启，列类型漂移也会被检出。
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 保证 `import app.*` 可用（alembic.ini 的 prepend_sys_path 只覆盖命令行场景）
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.database.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False：fileConfig 默认会禁用进程里已存在的
    # 全部 logger（含应用自身的 plasmid_designer.*），在同一进程内跑迁移
    # （tests/test_migrations.py、管理脚本）会静默吞掉后续日志
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _resolve_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    url = (x_args.get("db_url") or ""
           or os.environ.get("ALEMBIC_DATABASE_URL") or ""
           or settings.DATABASE_URL)
    if not url:
        raise RuntimeError("数据库地址为空：用 -x db_url= / ALEMBIC_DATABASE_URL / DATABASE_URL 提供")
    return url


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 脚本，不连库（--sql）"""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行"""
    configuration = config.get_section(config.config_ini_section, {}) or {}
    configuration["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite 不支持大多 ALTER，后续涉及改列的迁移需 batch 模式
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
