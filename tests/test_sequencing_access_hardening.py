"""匿名测序记录访问加固回归锁（令牌比较 / 遗留记录 / 整理包 / 令牌存储）"""

from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.routes import sequencing_routes as sr


def _fake_result():
    return {"engine": "internal", "conclusion": "ok", "reads": [], "variants": [],
            "consensus": {"sequence": "ACGT" * 20, "coverage_percent": 100.0},
            "coverage_ranges": [], "errors": [], "traces": []}


def _anon_record():
    return sr._register_analysis("x", "ACGT" * 20, [], _fake_result(), owner_id=None)


def test_non_ascii_token_is_403_not_500():
    aid, _tok, _ = _anon_record()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get(f"/api/sequencing/analyses/{aid}",
              headers={"X-Access-Token": "\u00e9t\u00e9".encode("utf-8")})
    assert r.status_code == 403
    assert not sr._can_access({"owner_id": None, "access_token": "abc"}, None, "\u00e9t\u00e9")


# ---------------- 无 token 遗留记录 ----------------

def _legacy_record(aid):
    from app import sequencing_store
    rec = {"analysis_id": aid, "sample_name": "old", "reference": "ACGT" * 20,
           "features": [], "created_at": datetime.now().isoformat(), "owner_id": None,
           **_fake_result(), "_trace_data": {}}
    rec.pop("traces", None)
    sequencing_store.persist_record(rec)
    return rec


def _admin_headers():
    import uuid
    from app.auth.jwt_auth import create_access_token, db_user_to_user, hash_password
    from app.database import SessionLocal, create_user
    email = f"adm-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        u = create_user(db, email=email, username=email.split("@")[0],
                        hashed_password=hash_password("Passw0rd!!"), is_admin=True,
                        email_verified=True)
        return {"Authorization": f"Bearer {create_access_token(db_user_to_user(u))}"}
    finally:
        db.close()


def test_legacy_tokenless_record_not_public():
    _legacy_record("seq_legacyrc0001")
    sr._ANALYSES.pop("seq_legacyrc0001", None)
    c = TestClient(app)
    base = "/api/sequencing/analyses/seq_legacyrc0001"
    assert c.get(base).status_code == 403
    assert c.delete(base).status_code == 403
    adm = _admin_headers()
    assert c.get(base, headers=adm).status_code == 200
    assert c.delete(base, headers=adm).status_code == 200


def test_legacy_tokenless_record_not_listed_in_memory_mode(monkeypatch):
    from app import sequencing_store
    monkeypatch.setattr(sequencing_store, "db_enabled", lambda: False)
    rec = dict(_legacy_record("seq_legacyrc0002"), _created_ts=__import__("time").time())
    rec["access_token"] = None
    monkeypatch.setitem(sr._ANALYSES, "seq_legacyrc0002", rec)
    c = TestClient(app)
    ids = [x["analysis_id"] for x in c.get("/api/sequencing/analyses").json()]
    assert "seq_legacyrc0002" not in ids


# ---------------- 令牌只认请求头 ----------------

def test_query_string_token_rejected():
    aid, tok, _ = _anon_record()
    c = TestClient(app)
    base = f"/api/sequencing/analyses/{aid}"
    assert c.get(base, params={"token": tok}).status_code == 403
    assert c.get(base + "/consensus/export", params={"token": tok}).status_code == 403
    assert c.get(base, headers={"X-Access-Token": tok}).status_code == 200


# ---------------- 令牌只存摘要 ----------------

def test_token_stored_as_sha256_hash_only():
    import hashlib
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB
    aid, tok, rec = _anon_record()
    assert "access_token" not in rec
    assert rec["access_token_hash"] == hashlib.sha256(tok.encode()).hexdigest()
    db = SessionLocal()
    try:
        row = db.get(SequencingAnalysisDB, aid)
        assert tok not in row.payload
    finally:
        db.close()
    sr._ANALYSES.pop(aid, None)  # 强制走库回灌
    c = TestClient(app)
    assert c.get(f"/api/sequencing/analyses/{aid}", headers={"X-Access-Token": tok}).status_code == 200
    assert c.get(f"/api/sequencing/analyses/{aid}",
                 headers={"X-Access-Token": rec["access_token_hash"]}).status_code == 403


def _plaintext_legacy_row(aid, tok):
    """模拟升级前落库的记录：payload 内明文 access_token"""
    from app import sequencing_store
    rec = {"analysis_id": aid, "sample_name": "old", "reference": "ACGT" * 20,
           "features": [], "created_at": datetime.now().isoformat(), "owner_id": None,
           "access_token": tok, **_fake_result(), "_trace_data": {}}
    rec.pop("traces", None)
    sequencing_store.persist_record(rec)


def test_plaintext_token_rows_migrated_and_still_usable():
    from app import sequencing_store
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB
    tok = "legacy-plaintext-token-0001"
    _plaintext_legacy_row("seq_plainrc00001", tok)
    assert sequencing_store.migrate_plaintext_tokens() >= 1
    assert sequencing_store.migrate_plaintext_tokens() == 0  # 幂等
    db = SessionLocal()
    try:
        assert tok not in db.get(SequencingAnalysisDB, "seq_plainrc00001").payload
    finally:
        db.close()
    sr._ANALYSES.pop("seq_plainrc00001", None)
    c = TestClient(app)
    base = "/api/sequencing/analyses/seq_plainrc00001"
    assert c.get(base).status_code == 403
    assert c.get(base, headers={"X-Access-Token": tok}).status_code == 200


def test_unmigrated_plaintext_row_still_works_via_lazy_hash():
    tok = "legacy-plaintext-token-0002"
    _plaintext_legacy_row("seq_plainrc00002", tok)
    sr._ANALYSES.pop("seq_plainrc00002", None)
    c = TestClient(app)
    base = "/api/sequencing/analyses/seq_plainrc00002"
    assert c.get(base, headers={"X-Access-Token": "wrong"}).status_code == 403
    assert c.get(base, headers={"X-Access-Token": tok}).status_code == 200
