"""批量测序 read 上限：分组（访问层级）设置与个人覆盖

生效顺序：个人覆盖 → 所在分组设置 → 服务器默认（环境变量），并按绝对安全
上限截断。超限 413 的提示须写明生效的上限值与来源；存量库升级时新列自动
补齐且为 NULL（= 跟随上一级，行为与升级前一致）。
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app import batch_read_limits as brl
from app import sequencing_batch, site_settings
from app.database import Base
from app.database import models as db_models
from app.features import ALL_FEATURES
from app.main import app
from app.routes import sequencing_routes as sr
from tests.abif_utils import make_ab1

REF_A = "ATGAAACGT" * 12 + "TAA"


def _site(limits=None):
    return {"registration_open": True, "email_verification_required": False,
            "anonymous_features": list(ALL_FEATURES), "user_features": list(ALL_FEATURES),
            "batch_read_limits": limits or brl.empty_tier_limits()}


def _user(is_admin=False, per_group=None, total=None):
    return SimpleNamespace(is_admin=is_admin, batch_max_reads_per_group=per_group,
                           batch_max_total_reads=total)


# ==================== 生效值解析 ====================

def test_defaults_when_nothing_set():
    eff = brl.effective_limits(_site(), None, defaults=(24, 240))
    assert eff[brl.PER_GROUP]["value"] == 24 and eff[brl.PER_GROUP]["source"] == "default"
    assert eff[brl.TOTAL]["value"] == 240 and eff[brl.TOTAL]["source"] == "default"


def test_tier_values_apply_per_tier():
    limits = brl.empty_tier_limits()
    limits["anonymous"][brl.TOTAL] = 50
    limits["user"][brl.TOTAL] = 600
    limits["admin"][brl.PER_GROUP] = 96
    site = _site(limits)
    assert brl.effective_limits(site, None, (24, 240))[brl.TOTAL]["value"] == 50
    u = brl.effective_limits(site, _user(), (24, 240))
    assert u[brl.TOTAL] == {"value": 600, "source": "tier", "tier": "user"}
    assert u[brl.PER_GROUP]["source"] == "default"       # 未设的字段各自回落
    a = brl.effective_limits(site, _user(is_admin=True), (24, 240))
    assert a[brl.PER_GROUP]["value"] == 96 and a[brl.TOTAL]["value"] == 240


def test_user_override_beats_tier_both_directions():
    limits = brl.empty_tier_limits()
    limits["user"][brl.TOTAL] = 600
    site = _site(limits)
    higher = brl.effective_limits(site, _user(total=1200), (24, 240))
    assert higher[brl.TOTAL] == {"value": 1200, "source": "user", "tier": "user"}
    lower = brl.effective_limits(site, _user(total=10, per_group=4), (24, 240))
    assert lower[brl.TOTAL]["value"] == 10 and lower[brl.PER_GROUP]["value"] == 4
    # 管理员同样可被单独设置
    adm = brl.effective_limits(site, _user(is_admin=True, total=7), (24, 240))
    assert adm[brl.TOTAL]["source"] == "user"


def test_hard_max_clamps_every_source(monkeypatch):
    monkeypatch.setattr(brl, "HARD_MAX_TOTAL_READS", 300)
    limits = brl.empty_tier_limits()
    limits["user"][brl.TOTAL] = 1000                    # 安全上限下调后的存量值
    eff = brl.effective_limits(_site(limits), _user(), (24, 240))
    assert eff[brl.TOTAL] == {"value": 300, "source": "hard_max", "tier": "user"}
    eff2 = brl.effective_limits(_site(), None, (24, 9999))
    assert eff2[brl.TOTAL]["value"] == 300


def test_garbage_stored_values_are_ignored():
    raw = {"anonymous": {brl.TOTAL: "300", brl.PER_GROUP: 0},
           "user": {brl.TOTAL: True}, "hacker": {brl.TOTAL: 9}}
    norm = brl.normalize_tier_limits(raw)
    assert norm == brl.empty_tier_limits()
    assert brl.effective_limits(_site(raw), None, (24, 240))[brl.TOTAL]["source"] == "default"


# ==================== 批量接口 413 ====================

def _files(n_reads):
    files = [("files", ("PA.fasta", f">PA\n{REF_A}\n".encode(), "application/octet-stream"))]
    files += [("files", (f"PA-{i}.ab1", make_ab1(REF_A, [40] * len(REF_A)),
                         "application/octet-stream")) for i in range(n_reads)]
    return files


@pytest.fixture
def batch_env(monkeypatch):
    state = {"site": _site()}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state["site"])))
    calls = []
    real = sequencing_batch.analyze
    monkeypatch.setattr(sequencing_batch, "analyze",
                        lambda *a, **kw: calls.append(1) or real(*a, **kw))
    return state, calls


def _db_user(per_group=None, total=None, is_admin=False):
    from app.auth.jwt_auth import create_access_token, db_user_to_user
    from app.database import SessionLocal
    from app.database.crud import create_user

    db = SessionLocal()
    try:
        email = f"lim-{uuid.uuid4().hex[:8]}@test.com"
        u = create_user(db, email=email, username="lim", hashed_password="x",
                        is_admin=is_admin, email_verified=True)
        u.batch_max_reads_per_group = per_group
        u.batch_max_total_reads = total
        db.commit()
        db.refresh(u)
        return u.id, create_access_token(db_user_to_user(u), token_version=0)
    finally:
        db.close()


def _post(files, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app).post("/api/sequencing/analyze-batch", files=files,
                                data={"min_q": "20"}, headers=headers)


def test_anonymous_tier_limit_enforced_with_source(batch_env):
    state, calls = batch_env
    limits = brl.empty_tier_limits()
    limits["anonymous"][brl.PER_GROUP] = 2
    state["site"] = _site(limits)
    r = _post(_files(3))
    assert r.status_code == 413, r.text
    detail = r.json()["detail"]
    assert "每组上限（2 条）" in detail and "「匿名访客」分组设置" in detail
    assert calls == []


def test_default_limit_message_names_server_default(batch_env, monkeypatch):
    monkeypatch.setattr(sr, "MAX_BATCH_TOTAL_READS", 2)
    r = _post(_files(3))
    assert r.status_code == 413, r.text
    assert "单次上限（2 条）" in r.json()["detail"]
    assert "服务器默认配置" in r.json()["detail"]


def test_user_tier_limit_raises_cap_above_default(batch_env, monkeypatch):
    state, calls = batch_env
    monkeypatch.setattr(sr, "MAX_BATCH_TOTAL_READS", 2)
    monkeypatch.setattr(sr, "MAX_BATCH_READS_PER_GROUP", 2)
    limits = brl.empty_tier_limits()
    limits["user"] = {brl.PER_GROUP: 3, brl.TOTAL: 3}
    state["site"] = _site(limits)
    _uid, token = _db_user()
    r = _post(_files(3), token)
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    # 同一批次匿名提交仍按服务器默认被拒
    assert _post(_files(3)).status_code == 413


def test_user_override_beats_tier_on_endpoint(batch_env):
    state, calls = batch_env
    limits = brl.empty_tier_limits()
    limits["user"] = {brl.PER_GROUP: 10, brl.TOTAL: 10}
    state["site"] = _site(limits)
    _uid, token = _db_user(total=2)
    r = _post(_files(3), token)
    assert r.status_code == 413, r.text
    assert "单次上限（2 条）" in r.json()["detail"]
    assert "本账号的单独设置" in r.json()["detail"]
    assert calls == []


def test_admin_tier_limit_applies_to_admins(batch_env):
    state, _calls = batch_env
    limits = brl.empty_tier_limits()
    limits["admin"][brl.TOTAL] = 2
    limits["user"][brl.TOTAL] = 100
    state["site"] = _site(limits)
    _uid, token = _db_user(is_admin=True)
    r = _post(_files(3), token)
    assert r.status_code == 413 and "「管理员」分组设置" in r.json()["detail"]


# ==================== 存量库升级 ====================

@pytest.fixture
def legacy_engine(tmp_path, monkeypatch):
    """模拟 v2.9.1 库：建表后删掉本次新增的三列"""
    eng = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    Base.metadata.create_all(bind=eng)
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO users (id, email, username, hashed_password, is_active, is_admin, "
            "email_verified, token_version) VALUES ('u1', 'a@b.c', 'a', 'x', 1, 0, 1, 0)"))
        c.execute(text(
            "INSERT INTO site_settings (id, registration_open, email_verification_required, "
            "anonymous_features, user_features, features_initialized, feature_migrations) "
            "VALUES (1, 1, 0, '[\"design\"]', '[\"design\"]', 1, 'sequencing_batch')"))
        c.execute(text("ALTER TABLE users DROP COLUMN batch_max_reads_per_group"))
        c.execute(text("ALTER TABLE users DROP COLUMN batch_max_total_reads"))
        c.execute(text("ALTER TABLE site_settings DROP COLUMN batch_read_limits"))
    monkeypatch.setattr(db_models, "engine", eng)
    yield eng
    eng.dispose()


def test_migration_adds_nullable_columns_idempotently(legacy_engine):
    for _ in range(2):                                  # 二次启动不重复 ALTER
        db_models._migrate_users_table()
        db_models._migrate_site_settings_table()
    insp = inspect(legacy_engine)
    ucols = {c["name"] for c in insp.get_columns("users")}
    scols = {c["name"] for c in insp.get_columns("site_settings")}
    assert {"batch_max_reads_per_group", "batch_max_total_reads"} <= ucols
    assert "batch_read_limits" in scols
    with legacy_engine.connect() as c:
        assert c.execute(text(
            "SELECT batch_max_reads_per_group, batch_max_total_reads FROM users")).one() == (None, None)
        raw = c.execute(text("SELECT batch_read_limits FROM site_settings")).scalar()
        anon = c.execute(text("SELECT anonymous_features FROM site_settings")).scalar()
    assert raw is None
    assert site_settings.parse_batch_read_limits(raw) == brl.empty_tier_limits()
    assert json.loads(anon) == ["design"]               # 既有设置不受影响
