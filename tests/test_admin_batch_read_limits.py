"""管理端配置批量测序 read 上限：分组设置与个人覆盖（仅管理员）

- GET/PUT /api/admin/settings 读写各分组两项上限，按项合并，null 清除
- PUT /api/admin/users/{id} 写个人覆盖，显式 null 清除，不传不改
- 取值须为 1..安全上限 的整数，否则 422 + 中文提示（手误不能拆掉保护）
- 非管理员一律拒绝
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app import batch_read_limits as brl
from app import site_settings
from app.main import app
from tests.abif_utils import make_ab1

REF_A = "ATGAAACGT" * 12 + "TAA"


def _mk_user(is_admin=False):
    from app.auth.jwt_auth import create_access_token, db_user_to_user
    from app.database import SessionLocal
    from app.database.crud import create_user

    db = SessionLocal()
    try:
        u = create_user(db, email=f"adm-{uuid.uuid4().hex[:8]}@test.com", username="u",
                        hashed_password="x", is_admin=is_admin, email_verified=True)
        return u.id, {"Authorization": f"Bearer {create_access_token(db_user_to_user(u))}"}
    finally:
        db.close()


def _db_user_cols(uid):
    from app.database import SessionLocal
    from app.database.crud import get_user_by_id

    db = SessionLocal()
    try:
        u = get_user_by_id(db, uid)
        return u.batch_max_reads_per_group, u.batch_max_total_reads
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _reset_limits():
    """本模块写真实（隔离）库的站点设置：前后都清空 read 上限，免得影响其他用例"""
    def _clear():
        site_settings.update_settings({"batch_read_limits": brl.empty_tier_limits()})
    _clear()
    yield
    _clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin(client):
    return _mk_user(is_admin=True)


# ==================== 权限 ====================

def test_anonymous_and_users_cannot_touch_limits(client):
    payload = {"batch_read_limits": {"anonymous": {brl.TOTAL: 4000}}}
    assert client.get("/api/admin/settings").status_code in (401, 403)
    assert client.put("/api/admin/settings", json=payload).status_code in (401, 403)
    uid, h = _mk_user()
    assert client.put("/api/admin/settings", json=payload, headers=h).status_code == 403
    r = client.put(f"/api/admin/users/{uid}", json={brl.USER_COLUMNS[brl.TOTAL]: 4000}, headers=h)
    assert r.status_code == 403
    assert _db_user_cols(uid) == (None, None)
    assert site_settings.get_settings(force_refresh=True)["batch_read_limits"] == brl.empty_tier_limits()


# ==================== 分组设置 ====================

def test_settings_expose_defaults_and_hard_max(client, admin):
    _uid, h = admin
    body = client.get("/api/admin/settings", headers=h).json()
    assert body["batch_read_limits"] == brl.empty_tier_limits()
    assert body["batch_read_limit_defaults"] == {brl.PER_GROUP: 24, brl.TOTAL: 240}
    assert body["batch_read_limit_hard_max"] == {brl.PER_GROUP: 384, brl.TOTAL: 5000}
    assert body["batch_upload_max_mb"] == 500
    assert body["batch_max_files"] == 400


def test_settings_merge_per_field_and_null_clears(client, admin):
    _uid, h = admin
    r = client.put("/api/admin/settings", headers=h,
                   json={"batch_read_limits": {"user": {brl.TOTAL: 600}}})
    assert r.status_code == 200, r.text
    assert r.json()["batch_read_limits"]["user"] == {brl.PER_GROUP: None, brl.TOTAL: 600}
    # 只改另一分组的另一字段：已有设置保持
    r = client.put("/api/admin/settings", headers=h,
                   json={"batch_read_limits": {"anonymous": {brl.PER_GROUP: 12}}})
    lim = r.json()["batch_read_limits"]
    assert lim["user"][brl.TOTAL] == 600 and lim["anonymous"][brl.PER_GROUP] == 12
    # 不带 batch_read_limits 的其他设置保存不影响上限
    r = client.put("/api/admin/settings", headers=h, json={"registration_open": True})
    assert r.json()["batch_read_limits"]["user"][brl.TOTAL] == 600
    # null 清除 → 回到服务器默认
    r = client.put("/api/admin/settings", headers=h,
                   json={"batch_read_limits": {"user": {brl.TOTAL: None}}})
    assert r.json()["batch_read_limits"]["user"][brl.TOTAL] is None
    assert client.get("/api/admin/settings", headers=h).json()["batch_read_limits"]["anonymous"][brl.PER_GROUP] == 12


@pytest.mark.parametrize("value,needle", [
    (0, "正整数"), (-5, "正整数"), (5001, "安全上限 5000"), ("300", "正整数"),
    (True, "正整数"), (12.5, "正整数"),
])
def test_settings_invalid_total_rejected_422(client, admin, value, needle):
    _uid, h = admin
    r = client.put("/api/admin/settings", headers=h,
                   json={"batch_read_limits": {"user": {brl.TOTAL: value}}})
    assert r.status_code == 422, r.text
    assert needle in r.text and "普通用户" in r.text
    assert site_settings.get_settings(force_refresh=True)["batch_read_limits"]["user"][brl.TOTAL] is None


def test_settings_unknown_tier_or_field_and_per_group_cap(client, admin):
    _uid, h = admin
    for payload in ({"vip": {brl.TOTAL: 10}}, {"user": {"max_files": 10}},
                    {"admin": {brl.PER_GROUP: 385}}):
        r = client.put("/api/admin/settings", headers=h, json={"batch_read_limits": payload})
        assert r.status_code == 422, (payload, r.text)
    assert client.put("/api/admin/settings", headers=h, json={
        "batch_read_limits": {"admin": {brl.PER_GROUP: 384, brl.TOTAL: 5000}}}).status_code == 200


def test_admin_saved_tier_limit_is_enforced(client, admin):
    _uid, h = admin
    client.put("/api/admin/settings", headers=h,
               json={"batch_read_limits": {"anonymous": {brl.PER_GROUP: 2}}})
    files = [("files", ("PA.fasta", f">PA\n{REF_A}\n".encode(), "application/octet-stream"))]
    files += [("files", (f"PA-{i}.ab1", make_ab1(REF_A, [40] * len(REF_A)),
                         "application/octet-stream")) for i in range(3)]
    r = client.post("/api/sequencing/analyze-batch", files=files, data={"min_q": "20"})
    assert r.status_code == 413, r.text
    assert "每组上限（2 条）" in r.json()["detail"] and "匿名访客" in r.json()["detail"]


# ==================== 个人覆盖 ====================

def test_user_override_set_keep_and_clear(client, admin):
    _aid, h = admin
    uid, _uh = _mk_user()
    col_t, col_g = brl.USER_COLUMNS[brl.TOTAL], brl.USER_COLUMNS[brl.PER_GROUP]
    r = client.put(f"/api/admin/users/{uid}", headers=h, json={col_t: 1000, col_g: 48})
    assert r.status_code == 200, r.text
    assert r.json()[col_t] == 1000 and r.json()[col_g] == 48
    listed = {u["id"]: u for u in client.get("/api/admin/users", headers=h).json()}
    assert listed[uid][col_t] == 1000
    # 改其他标记时不传 → 不动覆盖
    client.put(f"/api/admin/users/{uid}", headers=h, json={"email_verified": True})
    assert _db_user_cols(uid) == (48, 1000)
    # 显式 null → 清除（跟随分组）
    r = client.put(f"/api/admin/users/{uid}", headers=h, json={col_t: None})
    assert r.json()[col_t] is None and r.json()[col_g] == 48
    assert _db_user_cols(uid) == (48, None)


@pytest.mark.parametrize("col,value", [
    ("batch_max_total_reads", 5001), ("batch_max_total_reads", 0),
    ("batch_max_reads_per_group", 385), ("batch_max_reads_per_group", "abc"),
])
def test_user_override_invalid_rejected_422(client, admin, col, value):
    _aid, h = admin
    uid, _uh = _mk_user()
    r = client.put(f"/api/admin/users/{uid}", headers=h, json={col: value})
    assert r.status_code == 422, r.text
    assert "个人" in r.text
    assert _db_user_cols(uid) == (None, None)
