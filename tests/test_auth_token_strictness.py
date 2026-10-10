"""令牌严格性回归锁：携带了令牌却无效/过期 → 401；禁用用户 → 403

此前 get_current_user 把这些情况一律降级为匿名，受限用户只要弄坏
令牌即可按访客权限访问。/auth/verify 与 /auth/site-config 是探测会话
状态的公开端点，保持宽松（坏令牌 = 匿名）。
"""

import json
import uuid
from datetime import datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth.jwt_auth import (ALGORITHM, SECRET_KEY, create_access_token,
                               db_user_to_user, hash_password)
from app.database import SessionLocal, create_user, update_user
from app.main import app

client = TestClient(app)


def _user(**kw):
    email = f"tok-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        u = create_user(db, email=email, username=email.split("@")[0],
                        hashed_password=hash_password("Passw0rd!!"), email_verified=True)
        if kw:
            update_user(db, u.id, **kw)
            db.refresh(u)
        return db_user_to_user(u)
    finally:
        db.close()


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


PUBLIC = "/api/codon-tables"


def test_no_token_is_anonymous():
    assert client.get(PUBLIC).status_code == 200


def test_garbage_token_is_401():
    r = client.get(PUBLIC, headers=_h("garbage.token.value"))
    assert r.status_code == 401


def test_expired_token_is_401():
    u = _user()
    tok = jwt.encode({"sub": u.id, "exp": datetime.utcnow() - timedelta(minutes=1)},
                     SECRET_KEY, algorithm=ALGORITHM)
    assert client.get(PUBLIC, headers=_h(tok)).status_code == 401


def test_token_for_unknown_user_is_401():
    tok = jwt.encode({"sub": "no-such-user", "exp": datetime.utcnow() + timedelta(hours=1)},
                     SECRET_KEY, algorithm=ALGORITHM)
    assert client.get(PUBLIC, headers=_h(tok)).status_code == 401


def test_disabled_user_is_403():
    u = _user()
    tok = create_access_token(u)
    assert client.get(PUBLIC, headers=_h(tok)).status_code == 200
    db = SessionLocal()
    try:
        update_user(db, u.id, is_active=False)
    finally:
        db.close()
    assert client.get(PUBLIC, headers=_h(tok)).status_code == 403


def test_restricted_user_cannot_downgrade_to_anonymous_with_bad_token():
    u = _user(allowed_features=json.dumps(["design"]))
    tok = create_access_token(u)
    assert client.get("/api/sequencing/analyses", headers=_h(tok)).status_code == 403
    broken = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    assert client.get("/api/sequencing/analyses", headers=_h(broken)).status_code == 401


@pytest.mark.parametrize("path", ["/api/auth/verify", "/api/auth/site-config"])
def test_session_probe_endpoints_stay_soft(path):
    r = client.get(path, headers=_h("garbage.token.value"))
    assert r.status_code == 200
    if path.endswith("verify"):
        assert r.json() == {"valid": False, "user": None}
    else:
        assert r.json()["tier"] == "anonymous"
