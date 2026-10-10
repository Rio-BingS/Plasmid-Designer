"""浏览器会话 Cookie / CSRF / 服务端登出回归锁

令牌不再交给前端存 localStorage：登录/注册/验证成功时后端下发
httpOnly + SameSite=Lax 的会话 Cookie；Authorization: Bearer 头对
API / 脚本客户端继续有效（且优先于 Cookie）。Cookie 会话的写请求
须带 X-Requested-With；登出在服务端吊销令牌（jti 黑名单 + 令牌版本）。
"""

import uuid
from datetime import datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth.jwt_auth import ALGORITHM, AUTH_COOKIE_NAME, SECRET_KEY, hash_password
from app.config import settings
from app.database import (
    RevokedTokenDB, SessionLocal, create_user, get_user_by_email,
    prune_revoked_tokens, revoke_token, update_user,
)
from app.main import app

PASSWORD = "Passw0rd!!"
PUBLIC = "/api/codon-tables"


def _new_user():
    email = f"ck-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        create_user(db, email=email, username=email.split("@")[0],
                    hashed_password=hash_password(PASSWORD), email_verified=True)
    finally:
        db.close()
    return email


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r


def _set_cookie_header(resp) -> str:
    """会话 Cookie 的属性部分（去掉 name=value，避免令牌内容干扰断言）"""
    cookies = [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]
    matched = [c for c in cookies if c.startswith(f"{AUTH_COOKIE_NAME}=")]
    assert matched, f"响应未下发会话 Cookie: {cookies}"
    return matched[-1].split(";", 1)[1]


@pytest.fixture()
def client():
    return TestClient(app)


# ---------------------------------------------------------------- 下发属性


def test_login_sets_httponly_lax_cookie(client):
    r = _login(client, _new_user())
    header = _set_cookie_header(r).lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/api" in header
    assert "max-age=86400" in header
    # 纯 http 请求不置 Secure（否则本地开发浏览器不回传 Cookie）
    assert "secure" not in header
    # API / 脚本客户端仍可从响应体拿到 Bearer 令牌
    assert r.json()["access_token"]


def test_https_request_sets_secure(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", "auto")
    c = TestClient(app, base_url="https://testserver")
    assert "secure" in _set_cookie_header(_login(c, _new_user())).lower()


def test_forwarded_proto_trusted_only_from_trusted_proxy(client, monkeypatch):
    from app import rate_limit

    email = _new_user()
    h = {"X-Forwarded-Proto": "https"}
    # 对端不在 TRUSTED_PROXIES：转发头不可信
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=h)
    assert "secure" not in _set_cookie_header(r).lower()
    monkeypatch.setattr(rate_limit, "_is_trusted_proxy", lambda host: True)
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=h)
    assert "secure" in _set_cookie_header(r).lower()


@pytest.mark.parametrize("flag,base,expect", [
    ("true", "http://testserver", True),
    ("false", "https://testserver", False),
])
def test_secure_config_flag_overrides(monkeypatch, flag, base, expect):
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", flag)
    c = TestClient(app, base_url=base)
    header = _set_cookie_header(_login(c, _new_user())).lower()
    assert ("secure" in header) is expect


# ---------------------------------------------------------------- Cookie 鉴权


def test_cookie_alone_authenticates(client):
    email = _new_user()
    _login(client, email)
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == email
    v = client.get("/api/auth/verify").json()
    assert v["valid"] is True and v["user"]["email"] == email
    assert client.get("/api/auth/site-config").json()["tier"] == "user"


def test_bearer_header_still_works_without_cookie(client):
    token = _login(client, _new_user()).json()["access_token"]
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_bearer_header_takes_precedence_over_cookie(client):
    a = _new_user()
    b = _new_user()
    token_a = _login(client, a).json()["access_token"]
    _login(client, b)  # Cookie 现在属于 b
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    assert r.json()["email"] == a


def test_invalid_cookie_is_anonymous_on_optional_and_401_on_required(client):
    client.cookies.set(AUTH_COOKIE_NAME, "garbage.token.value", path="/api")
    # 可选认证：残留的坏 Cookie 不能让匿名访问处处 401
    assert client.get(PUBLIC).status_code == 200
    assert client.get("/api/auth/verify").json() == {"valid": False, "user": None}
    # 必须登录：401，前端据此清理登录态
    assert client.get("/api/auth/me").status_code == 401


def test_invalid_bearer_header_still_strict_401(client):
    r = client.get(PUBLIC, headers={"Authorization": "Bearer garbage.token.value"})
    assert r.status_code == 401


def test_logout_clears_cookie(client):
    _login(client, _new_user())
    r = client.post("/api/auth/logout", headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200
    header = _set_cookie_header(r).lower()
    assert "max-age=0" in header and "path=/api" in header
    assert client.get("/api/auth/me").status_code == 401


def test_logout_without_session_is_idempotent(client):
    r = client.post("/api/auth/logout", headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200


# ---------------------------------------------------------------- CSRF

CSRF = {"X-Requested-With": "XMLHttpRequest"}
WRITE = "/api/analysis/analyze"  # 任意写端点：CSRF 拒绝发生在路由之前
BODY = {"sequence": "ATGAAACGTTAA"}


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_cookie_write_without_csrf_header_rejected(client, method):
    _login(client, _new_user())
    r = getattr(client, method)(WRITE)
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]


def test_cookie_logout_without_csrf_header_rejected(client):
    _login(client, _new_user())
    assert client.post("/api/auth/logout").status_code == 403
    # 会话未被登出
    assert client.get("/api/auth/me").status_code == 200


def test_cookie_write_with_csrf_header_passes_csrf(client):
    _login(client, _new_user())
    assert client.post(WRITE, headers=CSRF, json=BODY).status_code == 200


def test_cookie_read_needs_no_csrf_header(client):
    _login(client, _new_user())
    assert client.get("/api/auth/me").status_code == 200


def test_bearer_write_needs_no_csrf_header(client):
    token = _login(client, _new_user()).json()["access_token"]
    # 即便同时带着 Cookie，Bearer 头请求也不做 CSRF 检查
    r = client.post(WRITE, headers={"Authorization": f"Bearer {token}"}, json=BODY)
    assert r.status_code == 200


def test_anonymous_write_needs_no_csrf_header(client):
    assert client.post(WRITE, json=BODY).status_code == 200


def test_relogin_with_stale_cookie_not_blocked_by_csrf(client):
    """登录类端点不读现有会话：Cookie 罐残留旧会话也能直接重新登录"""
    email = _new_user()
    _login(client, email)
    _login(client, email)  # 无 X-Requested-With 头


# ---------------------------------------------------------------- 服务端登出与吊销

def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def _user_id(email):
    db = SessionLocal()
    try:
        return get_user_by_email(db, email).id
    finally:
        db.close()


def test_logout_revokes_bearer_token(client):
    tok = _login(client, _new_user()).json()["access_token"]
    client.cookies.clear()
    assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 200
    assert client.post("/api/auth/logout", headers=_bearer(tok)).status_code == 200
    # 令牌被吊销：即使被人截获也不能再用（可选认证端点同样 401）
    assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 401
    assert client.get(PUBLIC, headers=_bearer(tok)).status_code == 401


def test_logout_revokes_cookie_session_server_side(client):
    _login(client, _new_user())
    stolen = client.cookies.get(AUTH_COOKIE_NAME)
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 200
    # 浏览器删掉了 Cookie；但截获的旧 Cookie 值重放也必须失效
    client.cookies.set(AUTH_COOKIE_NAME, stolen, path="/api")
    assert client.get("/api/auth/me").status_code == 401


def test_logout_only_revokes_that_session(client):
    email = _new_user()
    tok1 = _login(client, email).json()["access_token"]
    tok2 = _login(client, email).json()["access_token"]
    client.cookies.clear()
    client.post("/api/auth/logout", headers=_bearer(tok1))
    assert client.get("/api/auth/me", headers=_bearer(tok1)).status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(tok2)).status_code == 200


def test_logout_of_legacy_token_without_jti_bumps_version(client):
    email = _new_user()
    uid = _user_id(email)
    legacy = jwt.encode({"sub": uid, "email": email,
                         "exp": datetime.utcnow() + timedelta(hours=1)},
                        SECRET_KEY, algorithm=ALGORITHM)
    # 升级前签发的令牌（无 jti / tv）照常可用，升级不掉线
    assert client.get("/api/auth/me", headers=_bearer(legacy)).status_code == 200
    client.post("/api/auth/logout", headers=_bearer(legacy))
    assert client.get("/api/auth/me", headers=_bearer(legacy)).status_code == 401
    # 之后重新登录不受影响
    tok = _login(client, email).json()["access_token"]
    assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 200


def test_disable_invalidates_tokens_even_after_reenable(client):
    email = _new_user()
    tok = _login(client, email).json()["access_token"]
    client.cookies.clear()
    uid = _user_id(email)
    db = SessionLocal()
    try:
        update_user(db, uid, is_active=False)
        assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 403
        update_user(db, uid, is_active=True)
    finally:
        db.close()
    assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 401


def test_password_change_invalidates_tokens(client):
    email = _new_user()
    tok = _login(client, email).json()["access_token"]
    client.cookies.clear()
    db = SessionLocal()
    try:
        update_user(db, _user_id(email), hashed_password=hash_password("N3wPassw0rd!!"))
    finally:
        db.close()
    assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 401
    r = client.post("/api/auth/login", json={"email": email, "password": "N3wPassw0rd!!"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=_bearer(r.json()["access_token"])).status_code == 200


def test_unrelated_update_keeps_tokens_valid(client):
    email = _new_user()
    tok = _login(client, email).json()["access_token"]
    client.cookies.clear()
    db = SessionLocal()
    try:
        update_user(db, _user_id(email), email_verified=True, is_active=True)
    finally:
        db.close()
    assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 200


def test_revoked_list_pruned_by_expiry():
    db = SessionLocal()
    try:
        old = f"old-{uuid.uuid4().hex}"
        live = f"live-{uuid.uuid4().hex}"
        db.add(RevokedTokenDB(jti=old, expires_at=datetime.utcnow() - timedelta(minutes=1)))
        db.commit()
        # 写入新黑名单行时顺带清理过期行
        revoke_token(db, live, datetime.utcnow() + timedelta(hours=1))
        assert db.get(RevokedTokenDB, old) is None
        assert db.get(RevokedTokenDB, live) is not None
        # 到期后同样被清理
        assert prune_revoked_tokens(db, now=datetime.utcnow() + timedelta(hours=2)) >= 1
        assert db.get(RevokedTokenDB, live) is None
    finally:
        db.close()


def test_users_migration_adds_token_version(tmp_path, monkeypatch):
    """存量 users 表（无 token_version 列）轻量迁移补列，存量用户从 0 起"""
    from sqlalchemy import create_engine, inspect, text

    from app.database import models

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id VARCHAR(50) PRIMARY KEY, email VARCHAR(255), "
            "username VARCHAR(100), hashed_password VARCHAR(255), is_active BOOLEAN, "
            "is_admin BOOLEAN, email_verified BOOLEAN, allowed_features TEXT, "
            "created_at DATETIME)"))
        conn.execute(text("INSERT INTO users (id, email, username, hashed_password) "
                          "VALUES ('u1', 'a@b.c', 'a', 'x')"))
    monkeypatch.setattr(models, "engine", eng)
    models._migrate_users_table()
    models._migrate_users_table()  # 幂等
    assert "token_version" in {c["name"] for c in inspect(eng).get_columns("users")}
    with eng.connect() as conn:
        assert conn.execute(text("SELECT token_version FROM users")).scalar() == 0
