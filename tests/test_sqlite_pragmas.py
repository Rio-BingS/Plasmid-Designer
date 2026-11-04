"""SQLite 引擎并发写治理回归测试（WAL + busy_timeout，第二轮审计 B2）

默认 journal 模式下写锁独占，线程池 + 后台任务的并发写会互相
"database is locked"。事件监听器在每个新连接上强制三件套 PRAGMA。
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.database import models  # noqa: E402


pytestmark = pytest.mark.skipif(
    not models.DATABASE_URL.startswith("sqlite"),
    reason="仅 SQLite 方言（PostgreSQL 由服务端配置承担并发治理）")


def test_connection_pragmas():
    with models.engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA busy_timeout")).scalar() == 30000


def test_concurrent_writes_do_not_lock():
    """4 线程并发写同一张表：WAL + busy_timeout 下零 locked 错误。"""
    with models.engine.begin() as c:
        c.execute(text("CREATE TABLE IF NOT EXISTS _pragma_smoke"
                       "(id INTEGER PRIMARY KEY, v TEXT)"))
        c.execute(text("DELETE FROM _pragma_smoke"))
    errors = []

    def _writer(tag: str):
        try:
            with models.engine.begin() as c:
                for i in range(20):
                    c.execute(text("INSERT INTO _pragma_smoke(v) VALUES(:v)"),
                              {"v": f"{tag}-{i}"})
        except Exception as e:  # pragma: no cover - 治理失效时才走到
            errors.append(str(e))

    threads = [threading.Thread(target=_writer, args=(str(k),)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with models.engine.connect() as c:
        count = c.execute(text("SELECT COUNT(*) FROM _pragma_smoke")).scalar()
    with models.engine.begin() as c:
        c.execute(text("DROP TABLE _pragma_smoke"))
    assert not errors
    assert count == 80
