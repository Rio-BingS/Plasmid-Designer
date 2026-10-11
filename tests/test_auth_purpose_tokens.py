"""流程令牌（purpose）与登录令牌互不通用的回归锁（审查 07 M-2）

decode_token 拒绝带 purpose 的令牌是「验证令牌不能当登录令牌」的唯一
屏障：_user_from_token 只查 is_active / tv / jti，不查 email_verified；
验证令牌没有 tv（按 0，与新用户一致）也没有 jti（不会被判吊销），
这一行一旦在重构中丢失，注册后拿到的验证令牌就能直接登录、绕过验证码。
反方向：登录令牌也不能拿去调用验证流程接口。
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.auth.jwt_auth import (
    ALGORITHM, AUTH_COOKIE_NAME, SECRET_KEY, create_access_token,
    create_verify_token, db_user_to_user, decode_token, decode_verify_token,
    hash_password,
)
from app.auth.middleware import AuthStateMiddleware
from app.database import SessionLocal, create_user
from app.main import app

PUBLIC = "/api/codon-tables"  # 可选认证端点：带了令牌就必须有效


def _unverified_user():
    """注册后尚未验证邮箱的新用户（token_version=0，与无 tv 令牌相等）"""
    email = f"pur-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        u = create_user(db, email=email, username=email.split("@")[0],
                        hashed_password=hash_password("Passw0rd!!"), email_verified=False)
        return db_user_to_user(u)
    finally:
        db.close()


def _purpose_token(user, purpose):
    """除 purpose 外与登录令牌字段齐全（含 jti/tv），只靠 purpose 区分"""
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": user.id, "email": user.email, "username": user.username,
        "jti": uuid.uuid4().hex, "tv": 0, "purpose": purpose,
        "exp": now + timedelta(minutes=30), "iat": now,
    }, SECRET_KEY, algorithm=ALGORITHM)


def _purpose_tokens(user):
    return [
        create_verify_token(user.id),
        _purpose_token(user, "verify_email"),
        _purpose_token(user, "reset_password"),
    ]


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------- 解码层


def test_decode_token_rejects_purpose_tokens():
    u = _unverified_user()
    for tok in _purpose_tokens(u):
        assert decode_token(tok) is None
    # 对照：同一用户的登录令牌可以解码
    assert decode_token(create_access_token(u)).user_id == u.id


def test_decode_verify_token_rejects_login_and_other_purpose_tokens():
    u = _unverified_user()
    assert decode_verify_token(create_verify_token(u.id)) == u.id
    assert decode_verify_token(create_access_token(u)) is None
    assert decode_verify_token(_purpose_token(u, "reset_password")) is None


def test_auth_state_middleware_ignores_purpose_tokens():
    """限流按用户计数的身份也不能由流程令牌冒充"""
    u = _unverified_user()

    class _Req:
        def __init__(self, tok):
            self.headers = Headers({"Authorization": f"Bearer {tok}"})
            self.cookies = {}

    for tok in _purpose_tokens(u):
        assert AuthStateMiddleware._resolve_user(_Req(tok)) is None
    # 对照：登录令牌能解析出身份（确认桩请求确实被读取）
    assert AuthStateMiddleware._resolve_user(_Req(create_access_token(u)))["id"] == u.id


# ---------------------------------------------------------------- 接口层


@pytest.mark.parametrize("kind", ["bearer", "cookie"])
def test_purpose_token_cannot_access_me(kind):
    u = _unverified_user()
    for tok in _purpose_tokens(u):
        c = TestClient(app)
        if kind == "bearer":
            r = c.get("/api/auth/me", headers=_bearer(tok))
        else:
            c.cookies.set(AUTH_COOKIE_NAME, tok, path="/api")
            r = c.get("/api/auth/me")
        assert r.status_code == 401, (kind, r.text)


def test_purpose_token_not_a_session_on_optional_or_probe_endpoints():
    u = _unverified_user()
    for tok in _purpose_tokens(u):
        c = TestClient(app)
        # Bearer：可选认证端点带坏令牌即 401（不降级为匿名，更不当成该用户）
        assert c.get(PUBLIC, headers=_bearer(tok)).status_code == 401
        assert c.get("/api/auth/verify", headers=_bearer(tok)).json() == {
            "valid": False, "user": None}
        # Cookie：会话探测端点视为匿名
        c.cookies.set(AUTH_COOKIE_NAME, tok, path="/api")
        assert c.get("/api/auth/verify").json()["valid"] is False
        assert c.get("/api/auth/site-config").json()["tier"] == "anonymous"


@pytest.mark.parametrize("path,body", [
    ("/api/auth/verify-email", {"code": "123456"}),
    ("/api/auth/resend-verification", {}),
])
def test_login_token_rejected_by_verification_endpoints(path, body):
    u = _unverified_user()
    r = TestClient(app).post(path, json={"verify_token": create_access_token(u), **body})
    assert r.status_code == 400, r.text
    assert AUTH_COOKIE_NAME not in r.cookies
