"""账号系统测试：站点设置 / 功能门控 / 管理员 API / 注册邮箱验证 / 管理员引导

隔离策略：
- 独立 SQLite 内存库 + FastAPI dependency_overrides[get_db]（认证/管理路由走会话注入）
- site_settings.get_settings / update_settings 打桩（门控读侧走模块级连接，绕过会话注入）
- 邮件发送打桩：从验证码邮件 HTML 中抠出 6 位数字用于后续校验
"""

import json
import re
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
from app import site_settings  # noqa: E402
from app.features import ALL_FEATURES, valid_features  # noqa: E402

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module", autouse=True)
def _create_tables():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """限流中间件按 IP 计数（auth 桶 5 次/分钟），模块内大量认证请求会互相挤爆；
    本模块把所有限流桶放大并清空既有计数（monkeypatch.setitem 修改的就是
    中间件实际读取的同一份 RATE_LIMITS dict）"""
    from app import rate_limit

    for key in list(rate_limit.RATE_LIMITS):
        monkeypatch.setitem(rate_limit.RATE_LIMITS, key, {"requests": 10 ** 9, "window": 60})
    rate_limit.limiter._requests.clear()
    yield
    rate_limit.limiter._requests.clear()


@pytest.fixture(autouse=True)
def _clean_tables():
    """模块级共享的内存库在每个用例后清空（邮箱唯一约束会跨用例冲突）"""
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

    # 门控读侧打桩：进程内可变设置副本
    state = {"s": {
        "registration_open": True,
        "email_verification_required": False,
        "anonymous_features": list(ALL_FEATURES),
        "user_features": list(ALL_FEATURES),
    }}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state["s"])))
    monkeypatch.setattr(site_settings, "update_settings",
                        lambda patch: (state["s"].update(patch), json.loads(json.dumps(state["s"])))[1])
    client = TestClient(app)
    yield client, state
    app.dependency_overrides.pop(get_db, None)


def _set_site_db(db, **fields):
    """直接改测试库中的站点设置行（auth 路由读的是会话注入的这张表）"""
    row = get_site_settings_row(db)
    for k, v in fields.items():
        setattr(row, k, v)
    save_site_settings_row(db, row)
    return row


def _make_user(db, email="u@test.com", password="password123", is_admin=False, email_verified=True):
    from app.auth.jwt_auth import hash_password
    return create_user(db, email=email, username=email.split("@")[0],
                       hashed_password=hash_password(password),
                       is_admin=is_admin, email_verified=email_verified)


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def captured_mail(monkeypatch):
    sent = []

    async def fake_send_email(to, subject, html):
        m = re.search(r"\d{6}", html)
        sent.append({"to": to, "code": m.group(0) if m else None})
        return True

    import app.auth.routes as auth_routes
    monkeypatch.setattr(auth_routes, "send_email", fake_send_email)
    return sent


# ==================== site-config 与注册开关 ====================

class TestSiteConfig:
    def test_default_all_open(self, client):
        c, _ = client
        r = c.get("/api/auth/site-config")
        assert r.status_code == 200
        data = r.json()
        assert data["tier"] == "anonymous"
        assert data["registration_open"] is True
        assert data["email_verification_required"] is False
        assert set(data["effective_features"]) == set(ALL_FEATURES)

    def test_tier_follows_token(self, client, db):
        c, _ = client
        u = _make_user(db, is_admin=False)
        db.commit()
        token = _login(c, u.email, "password123")["access_token"]
        data = c.get("/api/auth/site-config", headers=_auth_header(token)).json()
        assert data["tier"] == "user"


class TestRegistrationToggle:
    def test_closed_registration_rejected(self, client, db):
        c, _ = client
        _set_site_db(db, registration_open=False)
        r = c.post("/api/auth/register", json={
            "email": "new@test.com", "username": "new",
            "password": "password123", "confirm_password": "password123"})
        assert r.status_code == 403
        assert "关闭注册" in r.json()["detail"]


# ==================== 功能门控 ====================

class TestFeatureGating:
    def test_anonymous_blocked_when_closed(self, client):
        c, state = client
        state["s"]["anonymous_features"] = []
        r = c.get("/api/codon-tables")
        assert r.status_code == 403
        assert "未对未登录访客开放" in r.json()["detail"]

    def test_user_allowed_while_anonymous_blocked(self, client, db):
        c, state = client
        state["s"]["anonymous_features"] = []
        u = _make_user(db)
        db.commit()
        token = _login(c, u.email, "password123")["access_token"]
        r = c.get("/api/codon-tables", headers=_auth_header(token))
        assert r.status_code == 200

    def test_admin_bypasses_all(self, client, db):
        c, state = client
        state["s"]["anonymous_features"] = []
        state["s"]["user_features"] = []
        a = _make_user(db, email="root@test.com", is_admin=True)
        db.commit()
        token = _login(c, a.email, "password123")["access_token"]
        assert c.get("/api/codon-tables", headers=_auth_header(token)).status_code == 200
        assert c.get("/api/vectors").status_code == 403

    def test_valid_features_cleans_unknown_keys(self):
        assert valid_features(["codon", "bogus", "codon", "vectors"]) == ["vectors", "codon"]


# ==================== 注册邮箱验证 ====================

class TestEmailVerification:
    def _register(self, c, email="v@test.com"):
        return c.post("/api/auth/register", json={
            "email": email, "username": email.split("@")[0],
            "password": "password123", "confirm_password": "password123"})

    def test_register_without_verification_returns_token(self, client, db):
        c, _ = client
        r = self._register(c)
        assert r.status_code == 200
        data = r.json()
        assert data["access_token"] and not data["requires_verification"]
        me = c.get("/api/auth/me", headers=_auth_header(data["access_token"]))
        assert me.status_code == 200

    def test_full_verification_flow(self, client, db, captured_mail):
        c, state = client
        state["s"]["email_verification_required"] = True
        _set_site_db(db, email_verification_required=True)

        r = self._register(c)
        assert r.status_code == 200
        data = r.json()
        assert data["requires_verification"] is True and data["access_token"] is None
        assert data["mail_sent"] is True and captured_mail[0]["to"] == "v@test.com"
        # 注册后未验证：登录被拦截并引导验证
        r2 = c.post("/api/auth/login", json={"email": "v@test.com", "password": "password123"})
        assert r2.status_code == 403
        detail = r2.json()["detail"]
        assert detail["code"] == "EMAIL_NOT_VERIFIED" and detail["verify_token"]

        vtoken = data["verify_token"]
        # 错误验证码
        bad = c.post("/api/auth/verify-email", json={"verify_token": vtoken, "code": "000000"})
        assert bad.status_code == 400 and "验证码错误" in bad.json()["detail"]
        # 正确验证码 → 登录令牌
        ok = c.post("/api/auth/verify-email", json={"verify_token": vtoken, "code": captured_mail[0]["code"]})
        assert ok.status_code == 200, ok.text
        token = ok.json()["access_token"]
        assert c.get("/api/auth/me", headers=_auth_header(token)).json()["email_verified"] is True
        # 验证后可正常登录
        assert c.post("/api/auth/login", json={
            "email": "v@test.com", "password": "password123"}).status_code == 200

    def test_resend_has_cooldown(self, client, db, captured_mail):
        c, state = client
        state["s"]["email_verification_required"] = True
        _set_site_db(db, email_verification_required=True)
        data = self._register(c, "r@test.com").json()
        r = c.post("/api/auth/resend-verification", json={"verify_token": data["verify_token"]})
        assert r.status_code == 429

    def test_verify_token_reuse_of_expired_or_bad(self, client, db, captured_mail):
        c, state = client
        state["s"]["email_verification_required"] = True
        _set_site_db(db, email_verification_required=True)
        r = c.post("/api/auth/verify-email", json={"verify_token": "not-a-token", "code": "123456"})
        assert r.status_code == 400

    def test_send_failure_returns_flag(self, client, db, monkeypatch):
        c, state = client
        state["s"]["email_verification_required"] = True
        _set_site_db(db, email_verification_required=True)

        async def failing_send(to, subject, html):
            return False

        import app.auth.routes as auth_routes
        monkeypatch.setattr(auth_routes, "send_email", failing_send)
        r = self._register(c, "f@test.com")
        assert r.status_code == 200
        assert r.json()["mail_sent"] is False and r.json()["verify_token"]


# ==================== 管理员 API ====================

class TestAdminApi:
    def test_admin_endpoints_require_admin(self, client, db):
        c, _ = client
        assert c.get("/api/admin/users").status_code in (401, 403)
        u = _make_user(db)
        db.commit()
        token = _login(c, u.email, "password123")["access_token"]
        r = c.get("/api/admin/users", headers=_auth_header(token))
        assert r.status_code == 403

    def test_settings_roundtrip(self, client, db):
        c, _ = client
        a = _make_user(db, email="root@test.com", is_admin=True)
        db.commit()
        h = _auth_header(_login(c, a.email, "password123")["access_token"])
        r = c.get("/api/admin/settings", headers=h)
        assert r.status_code == 200
        assert r.json()["feature_labels"]["design"] == "引物/序列设计"
        r2 = c.put("/api/admin/settings", headers=h, json={
            "registration_open": False,
            "anonymous_features": ["vectors", "codon"]})
        assert r2.status_code == 200
        assert r2.json()["registration_open"] is False
        assert r2.json()["anonymous_features"] == ["vectors", "codon"]

    def test_user_management_guards(self, client, db):
        c, _ = client
        a = _make_user(db, email="root@test.com", is_admin=True)
        b = _make_user(db, email="b@test.com")
        db.commit()
        h = _auth_header(_login(c, a.email, "password123")["access_token"])
        users = {u["email"]: u for u in c.get("/api/admin/users", headers=h).json()}
        assert set(users) == {"root@test.com", "b@test.com"}
        # 降级自己 = 最后一位管理员 → 拒绝
        r = c.put(f"/api/admin/users/{users['root@test.com']['id']}",
                  headers=h, json={"is_admin": False})
        assert r.status_code == 400
        # 禁用自己 → 拒绝
        r = c.put(f"/api/admin/users/{users['root@test.com']['id']}",
                  headers=h, json={"is_active": False})
        assert r.status_code == 400
        # 删除自己 → 拒绝
        r = c.delete(f"/api/admin/users/{users['root@test.com']['id']}", headers=h)
        assert r.status_code == 400
        # 正常操作：提升 b，然后禁用 b
        r = c.put(f"/api/admin/users/{users['b@test.com']['id']}",
                  headers=h, json={"is_admin": True, "email_verified": False})
        assert r.json()["is_admin"] is True and r.json()["email_verified"] is False
        # 删除 b 后用户列表只剩管理员
        c.delete(f"/api/admin/users/{users['b@test.com']['id']}", headers=h)
        rest = c.get("/api/admin/users", headers=h).json()
        assert [u["email"] for u in rest] == ["root@test.com"]

    def test_disabled_user_cannot_login(self, client, db):
        c, _ = client
        a = _make_user(db, email="root@test.com", is_admin=True)
        b = _make_user(db, email="b@test.com")
        db.commit()
        h = _auth_header(_login(c, a.email, "password123")["access_token"])
        bid = {u["email"]: u for u in c.get("/api/admin/users", headers=h).json()}["b@test.com"]["id"]
        c.put(f"/api/admin/users/{bid}", headers=h, json={"is_active": False})
        r = c.post("/api/auth/login", json={"email": "b@test.com", "password": "password123"})
        assert r.status_code == 403 and "禁用" in r.json()["detail"]


# ==================== 管理员引导 ====================

class TestAdminBootstrap:
    def test_bootstrap_creates_admin_from_env(self, monkeypatch, db):
        from app.config import settings as app_settings
        from app.database import SessionLocal as _unused  # noqa: F401 确认模块已加载
        from app.auth import bootstrap

        monkeypatch.setattr(app_settings, "ADMIN_EMAIL", "boss@test.com")
        monkeypatch.setattr(app_settings, "ADMIN_PASSWORD", "adminpass123")
        monkeypatch.setattr(app_settings, "ADMIN_USERNAME", "boss")
        monkeypatch.setattr("app.database.SessionLocal", TestingSession)
        assert bootstrap.bootstrap_admin() == "created"
        from app.database.crud import get_user_by_email
        u = get_user_by_email(TestingSession(), "boss@test.com")
        assert u is not None and u.is_admin and u.email_verified
        # 幂等：已存在且已是管理员 → 不再动作
        assert bootstrap.bootstrap_admin() is None

    def test_bootstrap_noop_without_env(self, monkeypatch):
        from app.config import settings as app_settings
        from app.auth import bootstrap

        monkeypatch.setattr(app_settings, "ADMIN_EMAIL", "")
        monkeypatch.setattr(app_settings, "ADMIN_PASSWORD", "")
        assert bootstrap.bootstrap_admin() is None
