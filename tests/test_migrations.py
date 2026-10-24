"""Alembic 迁移体系回归锁（审查清单「无正式迁移工具」）

验证三件事：
1. 空库 `upgrade head` 能完整建出与 SQLAlchemy 模型一致的表结构；
2. 升级后 autogenerate 对比差异为空——模型改动而没有对应迁移时此测试失败，
   防止「改了 models.py 忘了写迁移」的漂移；
3. 存量部署路径（init_db 的 create_all 建好的库）`stamp head` 正常——
   老库不重放建表迁移，只对齐版本号。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from app.database import models as db_models

BACKEND_DIR = Path(db_models.__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not (BACKEND_DIR / "alembic.ini").exists(), reason="alembic 未配置"
)


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    """指向独立临时库的 alembic 配置（不碰开发/生产库）"""
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite:///{tmp_path / 'mig.db'}")
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def _schema_diff(url: str) -> list:
    """模型 metadata 与库结构的 autogenerate 差异（空列表 = 完全一致）"""
    engine = create_engine(url)
    try:
        ctx = MigrationContext.configure(connection=engine.connect())
        return compare_metadata(ctx, db_models.Base.metadata)
    finally:
        engine.dispose()


def test_upgrade_head_matches_models(alembic_cfg, tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    command.upgrade(alembic_cfg, "head")
    diff = _schema_diff(url)
    assert diff == [], f"upgrade head 后与模型仍有差异: {diff}"


def test_stamp_head_on_legacy_create_all_db(alembic_cfg, tmp_path, monkeypatch):
    """存量路径：init_db/create_all 建出的库 stamp head 后不再重放建表"""
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite:///{tmp_path / 'mig.db'}")
    # 复刻存量库：直接 create_all（不走 alembic）
    engine = create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    db_models.Base.metadata.create_all(bind=engine)
    engine.dispose()

    command.stamp(alembic_cfg, "head")
    # stamp 后 upgrade head 应为 no-op（无待执行迁移），且结构仍与模型一致
    command.upgrade(alembic_cfg, "head")
    assert _schema_diff(f"sqlite:///{tmp_path / 'mig.db'}") == []
