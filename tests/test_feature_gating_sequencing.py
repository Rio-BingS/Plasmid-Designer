"""测序分析 / 批量测序分析功能门控拆分测试（sequencing × sequencing_batch）

覆盖：
- 注册表新键与排序（sequencing_batch 紧随 sequencing）
- 一次性拆分继承迁移：站点两级矩阵 + 用户个人覆盖，标记列保证只跑一次，
  且迁移后管理员的显式取消不被读路径补回
- API 门控拆分：单样品三入口属 sequencing、analyze-batch 属 sequencing_batch、
  分析记录查看为共用（任一开放即可）；site-config 的 effective_features 同步反映
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.database import Base, get_db  # noqa: E402
from app.database.crud import (  # noqa: E402
    create_user, get_site_settings_row, save_site_settings_row,
)
from app.database.models import _migrate_feature_lists  # noqa: E402
from app import site_settings  # noqa: E402
from app.auth.jwt_auth import hash_password  # noqa: E402
from app.features import ALL_FEATURES, FEATURE_REGISTRY  # noqa: E402

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module", autouse=True)
def _create_tables():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _no_rate_limit():
    from app import rate_limit

    rate_limit.limiter._requests.clear()
    yield
    rate_limit.limiter._requests.clear()


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    s = TestingSession()
    try:
        for t in reversed(Base.metadata.sorted_tables):
            s.execute(t.delete())
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def db():
    s = TestingSession()
    yield s
    s.rollback()
    s.close()


def _make_user(db, email="u@test.com", password="password123", is_admin=False,
               allowed_features=None):
    u = create_user(db, email=email, username=email.split("@")[0],
                    hashed_password=hash_password(password),
                    is_admin=is_admin, email_verified=True)
    if allowed_features is not None:
        u.allowed_features = json.dumps(allowed_features)
        db.commit()
    return u


# ---------------------------------------------------------------- 注册表与迁移


def test_registry_has_split_key_after_sequencing():
    keys = list(FEATURE_REGISTRY)
    assert "sequencing_batch" in keys
    assert keys.index("sequencing_batch") == keys.index("sequencing") + 1
    assert FEATURE_REGISTRY["sequencing_batch"]["label"] == "批量测序分析"
    assert "sequencing_batch" in ALL_FEATURES


def test_one_time_migration_inherits_split_key(db):
    """存量清单含 sequencing 无 sequencing_batch → 自动补齐并记标记；只跑一次

    迁移在 init_db 启动时执行（_migrate_feature_lists），这里直接以测试引擎调用；
    StaticPool 共享同一连接，ORM 会话需先提交再 expire 后重读。
    """
    old_keys = [k for k in ALL_FEATURES if k != "sequencing_batch"]
    row = get_site_settings_row(db)
    row.anonymous_features = json.dumps(old_keys)
    row.user_features = json.dumps([k for k in old_keys if k != "vectors"])  # 收紧过的站点
    row.feature_migrations = ""
    save_site_settings_row(db, row)

    _migrate_feature_lists(engine)
    db.expire_all()
    row = get_site_settings_row(db)
    assert "sequencing_batch" in json.loads(row.anonymous_features)
    assert "sequencing_batch" in json.loads(row.user_features)
    assert "vectors" not in json.loads(row.user_features)      # 未误开原有收紧项
    # 标记 = 已补齐的新键名集合
    assert "sequencing_batch" in row.feature_migrations

    # 幂等：再跑一次不变
    _migrate_feature_lists(engine)
    db.expire_all()
    row = get_site_settings_row(db)
    assert "vectors" not in json.loads(row.user_features)

    # 迁移后管理员显式取消勾选新键（保留 sequencing）→ 不会在下次启动被补回
    row.user_features = json.dumps([k for k in old_keys if k != "sequencing_batch"])
    save_site_settings_row(db, row)
    _migrate_feature_lists(engine)
    db.expire_all()
    row = get_site_settings_row(db)
    assert "sequencing_batch" not in json.loads(row.user_features)


def test_one_time_migration_covers_user_overrides(db):
    """用户个人覆盖清单同样补齐拆分键（get_current_user 每请求查库，重启后即时生效）"""
    old_keys = [k for k in ALL_FEATURES if k != "sequencing_batch"]
    _make_user(db, email="ov@test.com", allowed_features=old_keys)
    row = get_site_settings_row(db)
    row.feature_migrations = ""
    save_site_settings_row(db, row)

    _migrate_feature_lists(engine)
    db.expire_all()
    from app.database.models import UserDB
    u = db.query(UserDB).filter(UserDB.email == "ov@test.com").one()
    assert "sequencing_batch" in json.loads(u.allowed_features)


def test_migration_marker_survives_settings_save(db, monkeypatch):
    """管理员保存站点设置不会清掉迁移标记（save 全量落行，标记列随行保留）。

    update_settings 内部用模块级 SessionLocal——打桩到测试引擎，
    避免测试写入真实开发库。
    """
    import app.database as app_database

    monkeypatch.setattr(app_database, "SessionLocal", TestingSession)
    row = get_site_settings_row(db)
    row.feature_migrations = "sequencing_batch"
    save_site_settings_row(db, row)
    site_settings.update_settings({"registration_open": False})
    db.expire_all()
    row2 = get_site_settings_row(db)
    assert "sequencing_batch" in row2.feature_migrations
    assert row2.registration_open is False


# ---------------------------------------------------------------- API 门控拆分


@pytest.fixture()
def client(monkeypatch):
    def override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    from app.main import app

    app.dependency_overrides[get_db] = override_get_db

    state = {"s": {
        "registration_open": True,
        "email_verification_required": False,
        "anonymous_features": list(ALL_FEATURES),
        "user_features": list(ALL_FEATURES),
    }}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state["s"])))
    client = TestClient(app)
    yield client, state
    app.dependency_overrides.pop(get_db, None)


def _auth(client, email, password="password123"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_admin_settings_matrix_contains_split_key(client, db):
    """管理端功能矩阵返回新键（AdminView 据此渲染，无需前端改动）"""
    _make_user(db, email="admin@test.com", is_admin=True)
    c, _ = client
    r = c.get("/api/admin/settings", headers=_auth(c, "admin@test.com"))
    assert r.status_code == 200, r.text
    assert "sequencing_batch" in r.json()["feature_keys"]


def test_site_config_reflects_split_for_tier(client, db):
    """site-config 的 effective_features（前端导航/路由的唯一依据）按层级反映拆分。

    site-config 不走 get_settings() 桩，直接读注入会话的站点设置行，
    因此直接改测试库中的行。
    """
    _make_user(db, email="u2@test.com")
    c, _state = client
    row = get_site_settings_row(db)
    row.user_features = json.dumps([f for f in ALL_FEATURES if f != "sequencing_batch"])
    save_site_settings_row(db, row)
    headers = _auth(c, "u2@test.com")
    eff = c.get("/api/auth/site-config", headers=headers).json()["effective_features"]
    assert "sequencing" in eff and "sequencing_batch" not in eff


def test_analyze_endpoints_gated_independently(client, db):
    """单样品三入口属 sequencing；analyze-batch 属 sequencing_batch；共用查看任一即可"""
    _make_user(db, email="u3@test.com")
    c, state = client

    # 仅开放批量：单样品 403（依赖在参数解析前拒绝）、批量门控放行（400=无可用文件）、
    # 共用的分析记录列表可看
    state["s"]["user_features"] = [f for f in ALL_FEATURES if f != "sequencing"]
    headers = _auth(c, "u3@test.com")
    r = c.post("/api/sequencing/analyze",
               files={"reference": ("ref.fasta", b">x\nACGT\n", "application/octet-stream"),
                      "reads": ("r.ab1", b"x", "application/octet-stream")},
               headers=headers)
    assert r.status_code == 403 and "测序分析" in r.json()["detail"]
    r = c.post("/api/sequencing/analyze-batch", files={"files": ("说明.txt", b"x")},
               headers=headers)
    assert r.status_code == 400 and "未找到" in r.json()["detail"]  # 非 403：门控已放行
    assert c.get("/api/sequencing/analyses", headers=headers).status_code == 200

    # 仅开放单样品：批量 403、单样品门控放行（400=文件为空）、共用查看可看
    state["s"]["user_features"] = [f for f in ALL_FEATURES if f != "sequencing_batch"]
    r = c.post("/api/sequencing/analyze-batch", files={"files": ("说明.txt", b"x")},
               headers=headers)
    assert r.status_code == 403 and "批量测序分析" in r.json()["detail"]
    r = c.post("/api/sequencing/analyze",
               files={"reference": ("ref.fasta", b">x\nACGT\n", "application/octet-stream"),
                      "reads": ("r.ab1", b"invalid", "application/octet-stream")},
               headers=headers)
    assert r.status_code in (200, 400)  # 非 403：门控已放行（400=ab1 无效）

    # 两者都关：共用端点也 403；管理员不受限
    state["s"]["user_features"] = [
        f for f in ALL_FEATURES if f not in ("sequencing", "sequencing_batch")]
    assert c.get("/api/sequencing/analyses", headers=headers).status_code == 403
    _make_user(db, email="boss@test.com", is_admin=True)
    state["s"]["user_features"] = []
    assert c.get("/api/sequencing/analyses", headers=_auth(c, "boss@test.com")).status_code == 200
