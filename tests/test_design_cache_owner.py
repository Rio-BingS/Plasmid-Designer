"""设计结果缓存属主回归锁：旧版写入的「无属主」缓存条目不得绕过属主校验"""

import uuid
from datetime import datetime

from fastapi.testclient import TestClient

from app.auth.jwt_auth import create_access_token, db_user_to_user, hash_password
from app.cache import cache
from app.database import SessionLocal, create_user
from app.main import app
from app.routes import design_routes
from app.routes.models import DesignResult, DesignStatus


def _owner():
    email = f"dco-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        u = create_user(db, email=email, username=email.split("@")[0],
                        hashed_password=hash_password("Passw0rd!!"), email_verified=True)
        u = db_user_to_user(u)
    finally:
        db.close()
    return u, {"Authorization": f"Bearer {create_access_token(u)}"}


def _design(did, uid):
    return DesignResult(design_id=did, status=DesignStatus.COMPLETED, input_sequence="M",
                        optimized_sequence="ATG", construct_sequence="ATG", vector_id="pET-28a",
                        cloning_method="gibson", created_at=datetime.now(), user_id=uid)


def test_stale_ownerless_cache_entry_rechecked_against_store():
    u, h = _owner()
    d = _design("design_dco_00001", u.id)
    design_routes._persist(d)
    design_routes.designs_db.clear()
    stale = d.model_dump(mode="json")
    stale["user_id"] = None
    cache.cache_design_result(d.design_id, stale)        # 旧版写入的形态
    c = TestClient(app)
    assert c.get(f"/api/design/{d.design_id}").status_code == 403
    assert cache.get_design_result(d.design_id) is None or \
        cache.get_design_result(d.design_id).get("user_id") == u.id
    r = c.get(f"/api/design/{d.design_id}", headers=h)
    assert r.status_code == 200 and r.json()["user_id"] == u.id


def test_truly_anonymous_design_still_served_from_cache():
    d = _design("design_dco_00002", None)
    design_routes._persist(d)
    c = TestClient(app)
    assert c.get(f"/api/design/{d.design_id}").status_code == 200
