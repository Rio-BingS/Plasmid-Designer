"""测试环境隔离回归锁（终审 G-01）：pytest 全套件必须零触碰仓库内真实数据。

历史事故（2026-10-08）：conftest 隔离缺失时，test_db_retention_prunes_oldest
把 MAX_DB_RECORDS 打桩为 2 在真实开发库上触发全表淘汰，
data/plasmid_designer.db 的测序分析记录被清空、users 表混入测试账号。
本文件锁住隔离本身——任何一条失败都意味着「测试又在写真实库」。
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _norm(url: str) -> str:
    return url.replace("\\", "/")


def test_test_env_database_url_is_isolated():
    """conftest 必须把 DATABASE_URL 强制指向临时目录。"""
    url = os.environ.get("DATABASE_URL", "")
    assert url, "conftest 必须设置 DATABASE_URL（测试不得依赖外部环境）"
    assert "plasmid-test-data-" in _norm(url), url


def test_app_engine_points_at_isolated_db():
    """app 引擎实际连接的库必须在临时目录（防「引擎先于环境变量初始化」回归）。"""
    from app.database import engine

    url = _norm(str(engine.url))
    assert "plasmid-test-data-" in url, url
    assert _norm(str(REPO_ROOT / "data")) not in url


def test_log_dir_isolated():
    """日志目录同样隔离：src/backend/logs/ 不再被测试进程写入。"""
    log_dir = os.environ.get("PLASMID_LOG_DIR", "")
    assert log_dir, "conftest 必须设置 PLASMID_LOG_DIR"
    assert "plasmid-test-data-" in _norm(log_dir), log_dir
