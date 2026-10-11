"""/health 数据库连通性回归锁（审查 03 H1 / 10 H1）

此前 /health 不查库：PostgreSQL 口令与数据卷不一致时 init_db 失败只告警，
/health 仍返回 healthy，deploy.sh/compose healthcheck 都据此判定部署成功。
"""

import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import app.database as database
import app.main as main
from app.main import app


def test_health_reports_db_ok():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    # 原有字段保持不变，db 为新增字段
    assert body["status"] == "healthy"
    assert body["storage_mode"]
    assert "timestamp" in body
    assert body["db"] == "ok"


def test_health_503_when_db_unreachable(monkeypatch, tmp_path):
    # 指向不存在目录下的 SQLite 文件：连接必然失败（模拟口令错误/库不可达）
    bad = create_engine("sqlite:///" + (tmp_path / "missing" / "x.db").as_posix())
    monkeypatch.setattr(database, "engine", bad)
    r = TestClient(app).get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["db"] == "error"
    assert body["status"] == "unhealthy"
    # 不向外泄露连接串/异常细节
    assert "sqlite" not in r.text and "missing" not in r.text


def test_health_db_check_times_out(monkeypatch):
    monkeypatch.setattr(main, "HEALTH_DB_TIMEOUT", 0.2)
    monkeypatch.setattr(main, "_db_ping", lambda: time.sleep(2))
    t0 = time.monotonic()
    r = TestClient(app).get("/health")
    assert time.monotonic() - t0 < 1.5
    assert r.status_code == 503
    assert r.json()["db"] == "error"
