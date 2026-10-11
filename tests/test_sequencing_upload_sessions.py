"""批量测序分块上传会话回归锁

Cloudflare 免费版单请求 100 MB 上限下，批量页须把一批文件拆成多个请求，
服务器拼成一个批次、只分析一次：
- 会话生命周期：建会话 → 多个分块 → finalize（与单请求入口结果一致）→ 删除暂存
- 属主：他人（另一账号 / 无令牌匿名）不能追加、提交、查询、删除
- 过期清理、孤儿任务清理、路径穿越、体积/文件数配额、read 上限在 finalize 执行
- 文件数（400）与单请求总体积（500 MB）不再约束分块批次
"""

import json
import os
import stat
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import rate_limit, sequencing_batch
from app import sequencing_upload_sessions as store
from app.main import app
from app.routes import sequencing_routes as sr
from app.routes import sequencing_session_routes as ssr
from tests.abif_utils import make_ab1

REF_A = "ATGAAACGT" * 12 + "TAA"
REF_B = "ATGCCCGGT" * 12 + "TAA"
BASE = "/api/sequencing/batch-sessions"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    from app import site_settings
    from app.features import ALL_FEATURES as _ALL

    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": list(_ALL), "user_features": list(_ALL)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))
    root = tmp_path / "sessions"
    monkeypatch.setenv("SEQUENCING_BATCH_SESSION_DIR", str(root))
    calls = []
    real = sequencing_batch.analyze
    monkeypatch.setattr(sequencing_batch, "analyze",
                        lambda *a, **kw: calls.append(1) or real(*a, **kw))
    return {"root": root, "calls": calls}


def _ref(name, seq):
    return (f"{name}.fasta", f">{name}\n{seq}\n".encode())


def _read(name, seq):
    return (name, make_ab1(seq, [40] * len(seq)))


def _two_plasmids():
    return [_ref("PA", REF_A), _ref("PB", REF_B),
            _read("PA-1.ab1", REF_A), _read("PA-2.ab1", REF_A),
            _read("PB-1.ab1", REF_B), ("说明.txt", b"notes")]


def _multipart(blobs):
    return [("files", (n, b, "application/octet-stream")) for n, b in blobs]


def _headers(token=None, bearer=None):
    h = {}
    if token:
        h["X-Access-Token"] = token
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    return h


def _create(c, bearer=None, manifest=None, excel=None):
    data = {"manifest": json.dumps(manifest)} if manifest is not None else {}
    files = [("excel", excel)] if excel else None
    return c.post(BASE, data=data, files=files, headers=_headers(bearer=bearer))


def _upload(c, sid, blobs, index, token=None, bearer=None):
    return c.post(f"{BASE}/{sid}/files", files=_multipart(blobs),
                  data={"chunk_index": str(index)}, headers=_headers(token, bearer))


def _finalize(c, sid, token=None, bearer=None, min_q=20):
    return c.post(f"{BASE}/{sid}/finalize", data={"min_q": str(min_q)},
                  headers=_headers(token, bearer))


def _session_upload(c, blobs, chunk=2, bearer=None):
    r = _create(c, bearer=bearer)
    assert r.status_code == 200, r.text
    body = r.json()
    sid, token = body["session_id"], body.get("access_token")
    for i in range(0, len(blobs), chunk):
        u = _upload(c, sid, blobs[i:i + chunk], i // chunk, token, bearer)
        assert u.status_code == 200, u.text
    return sid, token


def _stable(payload):
    """去掉每次运行都会变的字段（批次 id、时间、记录 id、令牌）"""
    p = json.loads(json.dumps(payload))
    for k in ("batch_id", "created_at", "access_token"):
        p.pop(k, None)
    for it in p["items"]:
        it.pop("analysis_id", None)
    return p


def _db_user():
    from app.auth.jwt_auth import create_access_token, db_user_to_user
    from app.database import SessionLocal
    from app.database.crud import create_user

    db = SessionLocal()
    try:
        u = create_user(db, email=f"sess-{uuid.uuid4().hex[:8]}@test.com", username="s",
                        hashed_password="x", is_admin=False, email_verified=True)
        return u.id, create_access_token(db_user_to_user(u), token_version=0)
    finally:
        db.close()


# ==================== 生命周期与一致性 ====================

def test_chunked_result_equals_single_request(env):
    c = TestClient(app)
    blobs = _two_plasmids()
    single = c.post("/api/sequencing/analyze-batch", files=_multipart(blobs),
                    data={"min_q": "20"})
    assert single.status_code == 200, single.text

    sid, token = _session_upload(c, blobs, chunk=2)
    assert token                                         # 匿名会话下发令牌
    r = _finalize(c, sid, token)
    assert r.status_code == 200, r.text
    chunked = r.json()
    assert _stable(chunked) == _stable(single.json())
    assert chunked["access_token"] and chunked["report_ready"] is True
    # 整理包与单请求入口一样可凭整批令牌下载
    rep = c.get(f"/api/sequencing/batches/{chunked['batch_id']}/report",
                headers=_headers(chunked["access_token"]))
    assert rep.status_code == 200 and rep.content[:2] == b"PK"
    # 分析完成后上传原件立即删除，状态可查询并带同一份结果
    assert not (env["root"] / sid / "f").exists()
    st = c.get(f"{BASE}/{sid}", headers=_headers(token)).json()
    assert st["state"] == "done" and st["result"]["batch_id"] == chunked["batch_id"]
    # 删除会话
    assert c.delete(f"{BASE}/{sid}", headers=_headers(token)).status_code == 200
    assert not (env["root"] / sid).exists()


def test_private_permissions_and_sequence_disk_names(env):
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    assert _upload(c, sid, [_ref("PA", REF_A)], 0, token).status_code == 200
    d = env["root"] / sid
    assert stat.S_IMODE(os.stat(env["root"]).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(d / "meta.json").st_mode) == 0o600
    stored = list((d / "f").iterdir())
    assert [p.name for p in stored] == ["000001"]        # 磁盘名与用户输入无关
    assert stat.S_IMODE(os.stat(stored[0]).st_mode) == 0o600
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["token_hash"] and token not in json.dumps(meta)   # 只存摘要


def test_duplicate_names_kept_like_single_request(env):
    c = TestClient(app)
    blobs = [_ref("PA", REF_A), _ref("PA", REF_A), _read("PA-1.ab1", REF_A)]
    single = c.post("/api/sequencing/analyze-batch", files=_multipart(blobs),
                    data={"min_q": "20"}).json()
    sid, token = _session_upload(c, blobs, chunk=1)
    chunked = _finalize(c, sid, token).json()
    assert _stable(chunked) == _stable(single)
    assert any("同名图谱" in u["reason"] for u in chunked["unmatched"])


def test_chunk_retry_is_idempotent(env):
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    first = _upload(c, sid, [_ref("PA", REF_A)], 0, token).json()
    again = _upload(c, sid, [_ref("PA", REF_A)], 0, token).json()
    assert first["duplicate"] is False and again["duplicate"] is True
    assert again["file_count"] == 1                      # 没有重复落盘


def test_finalize_twice_and_append_after_finalize_rejected(env):
    c = TestClient(app)
    sid, token = _session_upload(c, _two_plasmids())
    assert _finalize(c, sid, token).status_code == 200
    assert _finalize(c, sid, token).status_code == 409
    assert _upload(c, sid, [_ref("PC", REF_A)], 9, token).status_code == 409


def test_finalize_without_files_400(env):
    c = TestClient(app)
    r = _create(c)
    assert _finalize(c, r.json()["session_id"], r.json()["access_token"]).status_code == 400


def test_slow_finalize_returns_202_then_poll(env, monkeypatch):
    monkeypatch.setattr(ssr, "FINALIZE_WAIT", 0)
    with TestClient(app) as c:
        sid, token = _session_upload(c, _two_plasmids())
        r = _finalize(c, sid, token)
        assert r.status_code == 202, r.text
        assert r.json()["session_id"] == sid
        for _ in range(200):
            st = c.get(f"{BASE}/{sid}", headers=_headers(token)).json()
            if st["state"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert st["state"] == "done", st
        assert len(st["result"]["items"]) == 2
        # 分析进行中不能取消；完成后可以
        assert c.delete(f"{BASE}/{sid}", headers=_headers(token)).status_code == 200


def test_excel_uploaded_at_create_is_used(env):
    """信息表随建会话上传：归组/回填与单请求入口一致（克隆模式）"""
    from tests.test_sequencing_batch import _xlsx_clone

    excel = _xlsx_clone([("S1", "PA", ["M13F"]), ("S2", "PA", ["M13F"])])
    blobs = [_ref("PA", REF_A), _read("S1-M13F.ab1", REF_A), _read("S2-M13F.ab1", REF_A)]
    c = TestClient(app)
    single = c.post("/api/sequencing/analyze-batch",
                    files=_multipart(blobs) + [("excel", excel)], data={"min_q": "20"})
    assert single.status_code == 200, single.text
    r = _create(c, excel=excel,
                manifest=[{"name": n, "size": len(b)} for n, b in blobs])
    assert r.status_code == 200, r.text
    sid, token = r.json()["session_id"], r.json()["access_token"]
    for i, b in enumerate(blobs):
        assert _upload(c, sid, [b], i, token).status_code == 200
    chunked = _finalize(c, sid, token)
    assert chunked.status_code == 200, chunked.text
    assert chunked.json()["clone_mode"] is True
    assert _stable(chunked.json()) == _stable(single.json())


def test_bad_excel_rejected_at_create(env):
    c = TestClient(app)
    r = _create(c, excel=("表.xlsx", b"not an xlsx", "application/octet-stream"),
                manifest=[{"name": "PA.fasta", "size": 3}])
    assert r.status_code == 400 and "xlsx" in r.json()["detail"]
    r = _create(c, excel=("~$表.xlsx", b"x", "application/octet-stream"))
    assert r.status_code == 400 and "临时文件" in r.json()["detail"]


# ==================== 属主 ====================

def test_other_user_cannot_touch_session(env):
    c = TestClient(app)
    _a, tok_a = _db_user()
    _b, tok_b = _db_user()
    r = _create(c, bearer=tok_a)
    body = r.json()
    sid = body["session_id"]
    assert "access_token" not in body                    # 登录会话按账号归属
    assert _upload(c, sid, [_ref("PA", REF_A), _read("PA-1.ab1", REF_A)], 0,
                   bearer=tok_a).status_code == 200
    for bearer in (tok_b, None):
        assert _upload(c, sid, [_ref("PX", REF_B)], 1, bearer=bearer).status_code == 404
        assert _finalize(c, sid, bearer=bearer).status_code == 404
        assert c.get(f"{BASE}/{sid}", headers=_headers(bearer=bearer)).status_code == 404
        assert c.delete(f"{BASE}/{sid}", headers=_headers(bearer=bearer)).status_code == 404
    r = _finalize(c, sid, bearer=tok_a)
    assert r.status_code == 200, r.text
    assert "access_token" not in r.json()


def test_anonymous_session_needs_its_token(env):
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    blob = [_ref("PA", REF_A)]
    assert _upload(c, sid, blob, 0).status_code == 404
    assert _upload(c, sid, blob, 0, "wrong-token").status_code == 404
    weird = c.post(f"{BASE}/{sid}/files", files=_multipart(blob), data={"chunk_index": "0"},
                   headers={"X-Access-Token": "tök€n".encode("latin-1", "replace")})
    assert weird.status_code == 404                                  # 非 ASCII 不 500
    _u, bearer = _db_user()                                          # 登录用户也不行
    assert _upload(c, sid, blob, 0, token, bearer).status_code == 404
    assert _upload(c, sid, blob, 0, token).status_code == 200


def test_per_owner_open_session_cap(env, monkeypatch):
    monkeypatch.setattr(store, "MAX_OPEN_PER_OWNER", 1)
    c = TestClient(app)
    first = _create(c)
    assert first.status_code == 200
    second = _create(c)
    assert second.status_code == 429 and "上限" in second.json()["detail"]
    # 取消后可以再建
    c.delete(f"{BASE}/{first.json()['session_id']}",
             headers=_headers(first.json()["access_token"]))
    assert _create(c).status_code == 200


def test_total_open_session_cap(env, monkeypatch):
    monkeypatch.setattr(store, "MAX_OPEN_TOTAL", 1)
    c = TestClient(app)
    assert _create(c).status_code == 200
    _u, bearer = _db_user()
    assert _create(c, bearer=bearer).status_code == 503


# ==================== 过期清理 ====================

def _patch_meta(root, sid, **kw):
    p = root / sid / "meta.json"
    meta = json.loads(p.read_text(encoding="utf-8"))
    meta.update(kw)
    p.write_text(json.dumps(meta), encoding="utf-8")


def test_expired_session_rejected_and_swept(env):
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    _upload(c, sid, [_ref("PA", REF_A)], 0, token)
    _patch_meta(env["root"], sid, expires_ts=time.time() - 1)
    assert _upload(c, sid, [_ref("PB", REF_B)], 1, token).status_code == 410
    assert store.sweep() == 1
    assert not (env["root"] / sid).exists()
    assert c.get(f"{BASE}/{sid}", headers=_headers(token)).status_code == 404


def test_sweep_removes_orphaned_and_broken_dirs_keeps_live(env):
    c = TestClient(app)
    live = _create(c).json()["session_id"]
    _u, bearer = _db_user()
    orphan = _create(c, bearer=bearer).json()["session_id"]
    _patch_meta(env["root"], orphan, state="running", pid=2 ** 22 + 12345)
    broken = env["root"] / uuid.uuid4().hex
    broken.mkdir()
    os.utime(broken, (time.time() - store.SESSION_TTL - 10,) * 2)
    stray = env["root"] / "not-a-session"
    stray.mkdir()
    assert store.sweep() == 2
    assert (env["root"] / live).exists()
    assert not (env["root"] / orphan).exists() and not broken.exists()
    assert stray.exists()                                # 不认识的目录不碰


def test_status_reports_interrupted_job_of_this_process(env):
    """pid 是本进程但任务不在运行表里（重启后 pid 复用）→ 视为中断"""
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    _patch_meta(env["root"], sid, state="running", pid=os.getpid())
    st = c.get(f"{BASE}/{sid}", headers=_headers(token)).json()
    assert st["state"] == "failed" and "中断" in st["error"]["detail"]
    assert c.delete(f"{BASE}/{sid}", headers=_headers(token)).status_code == 200


# ==================== 文件名与配额 ====================

@pytest.mark.parametrize("bad", ["../evil.ab1", "a/b.ab1", "..\\x.ab1", ".."])
def test_path_traversal_names_rejected(env, bad):
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    u = _upload(c, sid, [(bad, b"data")], 0, token)
    assert u.status_code == 400 and "文件名不合法" in u.json()["detail"]
    assert list((env["root"] / sid / "f").iterdir()) == []
    assert not (env["root"] / "evil.ab1").exists()


@pytest.mark.parametrize("bad", ["x\x01.ab1", "x\x7f.ab1", "", " ", "a" * 256, "C:\\x.ab1"])
def test_validate_filename_rejects_control_and_paths(bad):
    with pytest.raises(store.SessionError):
        store.validate_filename(bad)
    assert store.validate_filename("S1 测序-M13F.ab1") == "S1 测序-M13F.ab1"


@pytest.mark.parametrize("sid", ["..", "..%2F..%2Fetc", "ABC", "0" * 31])
def test_malformed_session_id_404(env, sid):
    c = TestClient(app)
    assert c.get(f"{BASE}/{sid}").status_code == 404


def test_session_byte_cap_rolls_back_chunk(env, monkeypatch):
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    ref = _ref("PA", REF_A)
    monkeypatch.setattr(store, "SESSION_MAX_BYTES", len(ref[1]) * 2 + 10)
    assert _upload(c, sid, [ref], 0, token).status_code == 200
    over = _upload(c, sid, [_ref("PB", REF_B), _ref("PC", REF_B)], 1, token)
    assert over.status_code == 413 and "总体积超过上限" in over.json()["detail"]
    st = c.get(f"{BASE}/{sid}", headers=_headers(token)).json()
    assert st["file_count"] == 1                          # 失败分块整体回滚
    assert len(list((env["root"] / sid / "f").iterdir())) == 1
    assert _upload(c, sid, [_ref("PB", REF_B)], 1, token).status_code == 200


def test_chunk_file_count_and_byte_caps(env, monkeypatch):
    monkeypatch.setattr(store, "CHUNK_MAX_FILES", 2)
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    three = [_ref(f"P{i}", REF_A) for i in range(3)]
    assert _upload(c, sid, three, 0, token).status_code == 413
    monkeypatch.setattr(store, "CHUNK_MAX_BYTES", 50)
    assert _upload(c, sid, three[:1], 0, token).status_code == 413


def test_per_file_size_cap(env, monkeypatch):
    monkeypatch.setattr(sr, "MAX_FILE_SIZE", 64)
    c = TestClient(app)
    r = _create(c)
    sid, token = r.json()["session_id"], r.json()["access_token"]
    u = _upload(c, sid, [_read("PA-1.ab1", REF_A)], 0, token)
    assert u.status_code == 400 and "文件过大" in u.json()["detail"]


def test_single_request_file_and_byte_limits_do_not_apply(env, monkeypatch):
    """400 个文件 / 500 MB 只约束单请求入口；分块批次由会话配额与 read 上限治理"""
    monkeypatch.setattr(sr, "MAX_BATCH_FILES", 3)
    monkeypatch.setattr(sr, "MAX_BATCH_REQUEST_BYTES", 100)
    c = TestClient(app)
    blobs = _two_plasmids()
    assert c.post("/api/sequencing/analyze-batch", files=_multipart(blobs),
                  data={"min_q": "20"}).status_code in (400, 413)
    sid, token = _session_upload(c, blobs, chunk=2)
    r = _finalize(c, sid, token)
    assert r.status_code == 200, r.text
    assert [it["status"] for it in r.json()["items"]] == ["analyzed", "analyzed"]


# ==================== read 上限 ====================

def test_read_cap_enforced_at_finalize(env, monkeypatch):
    monkeypatch.setattr(sr, "MAX_BATCH_TOTAL_READS", 2)
    c = TestClient(app)
    sid, token = _session_upload(c, _two_plasmids())
    r = _finalize(c, sid, token)
    assert r.status_code == 413, r.text
    assert "共有 3 条" in r.json()["detail"] and "服务器默认配置" in r.json()["detail"]
    assert env["calls"] == []                             # 一条都没比对
    st = c.get(f"{BASE}/{sid}", headers=_headers(token)).json()
    assert st["state"] == "failed" and st["error"]["status_code"] == 413
    assert not (env["root"] / sid / "f").exists()          # 失败同样清理原件


def test_manifest_precheck_rejects_before_upload(env, monkeypatch):
    monkeypatch.setattr(sr, "MAX_BATCH_TOTAL_READS", 2)
    c = TestClient(app)
    manifest = [{"name": n, "size": len(b)} for n, b in _two_plasmids()]
    r = _create(c, manifest=manifest)
    assert r.status_code == 413 and "共有 3 条" in r.json()["detail"]
    assert not env["root"].exists() or list(env["root"].iterdir()) == []
    # 未达上限的清单可以建会话；空文件/临时文件与单请求入口一样不计入
    ok = [{"name": "PA.fasta", "size": 10}, {"name": "PA-1.ab1", "size": 10},
          {"name": "PA-2.ab1", "size": 10}, {"name": "PA-3.ab1", "size": 0},
          {"name": "~$PA-4.ab1", "size": 10}]
    assert _create(c, manifest=ok).status_code == 200


def test_manifest_size_and_name_validation(env, monkeypatch):
    c = TestClient(app)
    assert _create(c, manifest=[{"name": "../x.ab1", "size": 1}]).status_code == 400
    assert _create(c, manifest=[{"name": "x.ab1"}]).status_code == 400
    monkeypatch.setattr(store, "SESSION_MAX_BYTES", 100)
    r = _create(c, manifest=[{"name": "PA.fasta", "size": 60}, {"name": "PA-1.ab1", "size": 60}])
    assert r.status_code == 413 and "总体积" in r.json()["detail"]
    r = c.post(BASE, data={"manifest": "{bad"})
    assert r.status_code == 400


def test_user_override_read_cap_applies_at_finalize(env, monkeypatch):
    """管理员给账号调高的上限在 finalize 生效（同一批次匿名提交被拒）"""
    from app.database import SessionLocal
    from app.database.models import UserDB as DbUser

    monkeypatch.setattr(sr, "MAX_BATCH_TOTAL_READS", 2)
    uid, bearer = _db_user()
    db = SessionLocal()
    try:
        u = db.get(DbUser, uid)
        u.batch_max_total_reads = 3
        db.commit()
    finally:
        db.close()
    c = TestClient(app)
    sid, _ = _session_upload(c, _two_plasmids(), bearer=bearer)
    assert _finalize(c, sid, bearer=bearer).status_code == 200
    sid2, token2 = _session_upload(c, _two_plasmids())
    assert _finalize(c, sid2, token2).status_code == 413


# ==================== 限流档位 ====================

def test_rate_limit_buckets_for_session_endpoints():
    sid = uuid.uuid4().hex
    assert rate_limit.classify_endpoint("POST", BASE) == "upload"
    assert rate_limit.classify_endpoint("POST", f"{BASE}/{sid}/files") == "upload_chunk"
    assert rate_limit.classify_endpoint("POST", f"{BASE}/{sid}/finalize") == "batch"
    assert rate_limit.classify_endpoint("DELETE", f"{BASE}/{sid}") == "default"
    assert rate_limit.classify_endpoint("GET", f"{BASE}/{sid}") == "default"
    # 旧入口不受影响
    assert rate_limit.classify_endpoint("POST", "/api/sequencing/analyze-batch") == "upload"
    chunk = rate_limit.RATE_LIMITS["upload_chunk"]
    assert chunk["requests"] >= 100 and chunk["window"] == 3600


def test_feature_gate_applies(env, monkeypatch):
    from app import site_settings

    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": ["sequencing"], "user_features": ["sequencing"]}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))
    assert TestClient(app).post(BASE).status_code == 403


def test_unwritable_session_dir_gives_clear_503(env, monkeypatch, tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    monkeypatch.setenv("SEQUENCING_BATCH_SESSION_DIR", str(blocker / "sub"))
    r = _create(TestClient(app))
    assert r.status_code == 503 and "暂存目录不可写" in r.json()["detail"]
