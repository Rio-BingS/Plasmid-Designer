"""浏览器会话 Cookie 回归锁

令牌不再交给前端存 localStorage：登录/注册/验证成功时后端下发
httpOnly + SameSite=Lax 的会话 Cookie；Authorization: Bearer 头对
API / 脚本客户端继续有效（且优先于 Cookie）。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt_auth import AUTH_COOKIE_NAME, hash_password
from app.config import settings
from app.database import SessionLocal, create_user
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
