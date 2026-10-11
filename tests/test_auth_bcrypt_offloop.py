"""bcrypt 不得在事件循环线程上同步执行（审查 02 M5）

bcrypt 12 轮单次约数百毫秒；在 async 路由里直接调用会让并发登录
把整个进程（含 /health 与其他请求）卡住。这里把路由引用的哈希/校验
函数换成探针：若在事件循环线程上被调用，asyncio.get_running_loop()
能拿到正在运行的 loop；放进线程池后则拿不到。
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import routes as auth_routes
from app.auth.jwt_auth import hash_password, verify_password
from app.database import SessionLocal, create_user, get_site_settings_row, save_site_settings_row
from app.main import app

PASSWORD = "Passw0rd!!"


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.fixture()
def calls(monkeypatch):
    seen = []

    def probe_verify(plain, hashed):
        seen.append(("verify", _on_event_loop()))
        return verify_password(plain, hashed)

    def probe_hash(plain):
        seen.append(("hash", _on_event_loop()))
        return hash_password(plain)

    monkeypatch.setattr(auth_routes, "verify_password", probe_verify)
    monkeypatch.setattr(auth_routes, "hash_password", probe_hash)
    return seen


def _new_user():
    email = f"bc-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        create_user(db, email=email, username=email.split("@")[0],
                    hashed_password=hash_password(PASSWORD), email_verified=True)
    finally:
        db.close()
    return email


def test_login_verifies_password_off_event_loop(calls):
    client = TestClient(app)
    email = _new_user()
    ok = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert ok.status_code == 200, ok.text
    bad = client.post("/api/auth/login", json={"email": email, "password": "wrong-pass"})
    assert bad.status_code == 401
    assert calls == [("verify", False), ("verify", False)]


def test_register_hashes_password_off_event_loop(calls, monkeypatch):
    db = SessionLocal()
    try:
        row = get_site_settings_row(db)
        old = (row.registration_open, row.email_verification_required)
        row.registration_open, row.email_verification_required = True, False
        save_site_settings_row(db, row)
        email = f"rg-{uuid.uuid4().hex[:8]}@example.com"
        r = TestClient(app).post("/api/auth/register", json={
            "email": email, "username": email.split("@")[0],
            "password": PASSWORD, "confirm_password": PASSWORD,
        })
        assert r.status_code in (200, 201), r.text
        assert calls == [("hash", False)]
    finally:
        row = get_site_settings_row(db)
        row.registration_open, row.email_verification_required = old
        save_site_settings_row(db, row)
        db.close()
