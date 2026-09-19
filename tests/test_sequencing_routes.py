"""测序分析路由集成测试（design 全链路 + 内存结果端点）"""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from abif_utils import make_ab1  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _all_features_open(monkeypatch):
    """端点门控读站点设置（模块级连接，本机真实库）——打桩为默认全开放，
    使测试不依赖本机数据库的矩阵状态（存量库可能缺新拆分键）"""
    from app import site_settings
    from app.features import ALL_FEATURES as _ALL

    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": list(_ALL), "user_features": list(_ALL)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))


@pytest.fixture(scope="module")
def completed_design(client):
    seq = "ATG" + "AAACAG" * 24 + "TAA"
    resp = client.post("/api/design", json={
        "sequence": seq,
        "sequence_type": "dna",
        "optimize_codons": False,
        "cloning_method": "restriction",
        "enzyme_5": "EcoRI",
        "enzyme_3": "HindIII",
        "sequence_name": "seqTest",
    })
    design_id = resp.json()["design_id"]
    for _ in range(60):
        d = client.get(f"/api/design/{design_id}").json()
        if d["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert d["status"] == "completed", d.get("errors")
    return d


def test_full_sequencing_flow(client, completed_design):
    design_id = completed_design["design_id"]
    ref = completed_design["construct_sequence"]

    # 图谱数据带酶切位点
    m = client.get(f"/api/design/{design_id}/map").json()
    assert m["length"] == len(ref)
    assert isinstance(m["enzyme_sites"], list)

    # 构造含 1 个替换的 ab1
    start = (completed_design.get("insert_start") or 1) - 1
    seg = list(ref[start:start + 500])
    seg[50] = "A" if seg[50] != "A" else "G"
    blob = make_ab1("".join(seg), [40] * 500)

    resp = client.post(
        f"/api/designs/{design_id}/sequencing/analyze",
        files={"files": ("r1.ab1", blob, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reads"][0]["identity"] > 0.99
    assert len(data["variants"]) == 1
    assert data["variants"][0]["type"] == "substitution"
    assert data["conclusion"]
    assert data["consensus"]["sequence"]
    assert "cds_reports" in data

    # 逐列对齐视图 + 共识差异位（人工核对证据）
    av = data["reads"][0]["alignment_view"]
    assert av and av["ref_aligned"]
    assert len(av["ref_aligned"]) == len(av["read_aligned"]) == len(av["q_aligned"])
    assert av["ref_aligned"].replace("-", "") in ref
    diffs = data["consensus"]["diffs"]
    assert len(diffs) == 1 and diffs[0]["ref_pos"] == data["variants"][0]["ref_pos"]
    assert data["consensus"]["sequence"][diffs[0]["cons_index"]] == diffs[0]["cons_base"]

    analysis_id = data["analysis_id"]

    # 结果摘要端点
    got = client.get(f"/api/sequencing/analyses/{analysis_id}").json()
    assert got["analysis_id"] == analysis_id

    # 历史列表端点（含刚完成的分析，删除后消失）
    listing = client.get("/api/sequencing/analyses").json()
    ids = [item["analysis_id"] for item in listing]
    assert analysis_id in ids
    item = next(i for i in listing if i["analysis_id"] == analysis_id)
    assert item["read_count"] >= 1
    assert item["reference_length"] == len(ref)
    assert "coverage_percent" in item and "conclusion" in item

    # 峰图端点
    trace = client.get(f"/api/sequencing/analyses/{analysis_id}/trace/0").json()
    assert set(trace["channels"].keys()) == {"A", "T", "G", "C"}
    assert trace["bases"]

    # 共识导出
    fasta = client.get(f"/api/sequencing/analyses/{analysis_id}/consensus/export?format=fasta").text
    assert fasta.startswith(">")
    gb = client.get(f"/api/sequencing/analyses/{analysis_id}/consensus/export?format=genbank").text
    assert gb.startswith("LOCUS")

    # 删除
    assert client.delete(f"/api/sequencing/analyses/{analysis_id}").status_code == 200
    assert client.get(f"/api/sequencing/analyses/{analysis_id}").status_code == 404


def test_analyze_rejects_non_ab1(client, completed_design):
    resp = client.post(
        f"/api/designs/{completed_design['design_id']}/sequencing/analyze",
        files={"files": ("x.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_analyze_unknown_design(client):
    blob = make_ab1("ACGT" * 25, [40] * 100)
    resp = client.post(
        "/api/designs/nonexistent/sequencing/analyze",
        files={"files": ("r.ab1", blob, "application/octet-stream")},
    )
    assert resp.status_code == 404


# ==================== 分析记录属主校验 ====================

def _ensure_user(email: str, is_admin: bool = False):
    """在真实开发库 get_or_create 测试用户（固定邮箱可重复运行）"""
    from app.database import SessionLocal
    from app.database.crud import create_user, get_user_by_email
    from app.auth.jwt_auth import hash_password

    db = SessionLocal()
    try:
        u = get_user_by_email(db, email)
        if u is None:
            u = create_user(db, email=email, username=email.split("@")[0],
                            hashed_password=hash_password("password123"),
                            is_admin=is_admin, email_verified=True)
            db.commit()
            db.refresh(u)
        return u
    finally:
        db.close()


def _login_header(client, email: str, password: str = "password123"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_analysis_record_ownership(client):
    """分析记录绑定创建者：非创建者读/导出/删除一律 403，列表看不到；
    管理员全可见；创建者本人不受影响（无属主的匿名遗留记录保持公开）"""
    ua = _ensure_user("owner-a@test.com")
    ub = _ensure_user("owner-b@test.com")
    admin = _ensure_user("owner-admin@test.com", is_admin=True)
    ha, hb, hadm = (_login_header(client, u.email) for u in (ua, ub, admin))

    ref = ("ref.fasta", b">ref\n" + b"ATG" * 40, "text/plain")
    blob = make_ab1("ATG" * 40, [40] * 120)
    ra = client.post("/api/sequencing/analyze",
                     files={"reference": ref, "reads": ("r1.ab1", blob, "application/octet-stream")},
                     headers=ha)
    assert ra.status_code == 200, ra.text
    aid = ra.json()["analysis_id"]

    # B：详情/峰图/导出/删除全部 403，列表里看不到 A 的记录
    assert client.get(f"/api/sequencing/analyses/{aid}", headers=hb).status_code == 403
    assert client.get(f"/api/sequencing/analyses/{aid}/trace/0", headers=hb).status_code == 403
    assert client.get(f"/api/sequencing/analyses/{aid}/consensus/export", headers=hb).status_code == 403
    assert client.delete(f"/api/sequencing/analyses/{aid}", headers=hb).status_code == 403
    assert all(x["analysis_id"] != aid
               for x in client.get("/api/sequencing/analyses", headers=hb).json())

    # 管理员可见；A 本人可见、列表可见、可删
    assert client.get(f"/api/sequencing/analyses/{aid}", headers=hadm).status_code == 200
    assert client.get(f"/api/sequencing/analyses/{aid}", headers=ha).status_code == 200
    assert any(x["analysis_id"] == aid
               for x in client.get("/api/sequencing/analyses", headers=ha).json())
    assert client.delete(f"/api/sequencing/analyses/{aid}", headers=ha).status_code == 200

    # 匿名创建的记录无属主 → 保持公开（历史行为，匿名分析本来无法归属）
    blob2 = make_ab1("AAG" * 40, [40] * 120)
    r2 = client.post("/api/sequencing/analyze",
                     files={"reference": ref, "reads": ("r2.ab1", blob2, "application/octet-stream")})
    assert r2.status_code == 200
    aid2 = r2.json()["analysis_id"]
    assert client.get(f"/api/sequencing/analyses/{aid2}", headers=ha).status_code == 200
    client.delete(f"/api/sequencing/analyses/{aid2}")
