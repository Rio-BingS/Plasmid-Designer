"""测序分析路由集成测试（design 全链路 + 内存结果端点）"""

import json
import os
import sys
import time
from typing import Dict

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
    # 匿名创建 → 携带创建响应下发的 access_token（终审 B-02）
    tok = _anon_token_header(resp)

    # 结果摘要端点
    got = client.get(f"/api/sequencing/analyses/{analysis_id}", headers=tok).json()
    assert got["analysis_id"] == analysis_id

    # 历史列表：匿名记录 ID 不进列表（B-02），不出现
    listing = client.get("/api/sequencing/analyses").json()
    ids = [item["analysis_id"] for item in listing]
    assert analysis_id not in ids

    # 峰图端点
    trace = client.get(f"/api/sequencing/analyses/{analysis_id}/trace/0",
                       headers=tok).json()
    assert set(trace["channels"].keys()) == {"A", "T", "G", "C"}
    assert trace["bases"]

    # 共识导出
    fasta = client.get(f"/api/sequencing/analyses/{analysis_id}/consensus/export?format=fasta",
                       headers=tok).text
    assert fasta.startswith(">")
    gb = client.get(f"/api/sequencing/analyses/{analysis_id}/consensus/export?format=genbank",
                    headers=tok).text
    assert gb.startswith("LOCUS")

    # 删除
    assert client.delete(f"/api/sequencing/analyses/{analysis_id}",
                         headers=tok).status_code == 200
    assert client.get(f"/api/sequencing/analyses/{analysis_id}").status_code == 404


def test_reference_length_capped(client, monkeypatch):
    """终审 C-01 回归锁：参考序列此前只校验 ≥50bp 无上限——局部比对是
    O(len(ref)×len(read)) 且正反向各做一次完整回溯，实测 300 kb 参考 ×
    1 条 read = 11 s / 1.09 GB。超过 MAX_REF_BP 必须被拒绝。"""
    import app.routes.sequencing_routes as seq_routes

    monkeypatch.setattr(seq_routes, "MAX_REF_BP", 1000)
    blob = make_ab1("ATG" * 100, [40] * 300)

    # 单样品上传入口
    too_long = ">ref\n" + "ACGT" * 400   # 1600bp > 1000
    r = client.post("/api/sequencing/analyze",
                    files={"reference": ("ref.fasta", too_long, "text/plain"),
                           "reads": ("r1.ab1", blob, "application/octet-stream")})
    assert r.status_code == 413, r.text
    assert "过长" in r.json()["detail"]

    # 上限之内的请求照常受理（不误伤）
    ok = ">ref\n" + "ACGT" * 100  # 400bp
    r3 = client.post("/api/sequencing/analyze",
                     files={"reference": ("ref.fasta", ok, "text/plain"),
                            "reads": ("r1.ab1", blob, "application/octet-stream")})
    assert r3.status_code == 200, r3.text


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


def _anon_token_header(resp) -> Dict[str, str]:
    """终审 B-02：匿名创建响应下发的 access_token → 后续请求头。"""
    tok = resp.json().get("access_token")
    assert tok, f"匿名创建应下发 access_token: {resp.json().get('analysis_id')}"
    return {"X-Access-Token": tok}


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

    # 匿名创建的记录 → 凭创建响应下发的 access_token 访问（终审 B-02）
    blob2 = make_ab1("AAG" * 40, [40] * 120)
    r2 = client.post("/api/sequencing/analyze",
                     files={"reference": ref, "reads": ("r2.ab1", blob2, "application/octet-stream")})
    assert r2.status_code == 200
    aid2 = r2.json()["analysis_id"]
    tok2 = _anon_token_header(r2)
    # 无令牌的他人（登录用户 B）不可见；持令牌可读、列表不可见（令牌是持有者凭证）、可删
    assert client.get(f"/api/sequencing/analyses/{aid2}", headers=ha).status_code == 403
    assert client.get(f"/api/sequencing/analyses/{aid2}", headers=tok2).status_code == 200
    assert all(x["analysis_id"] != aid2
               for x in client.get("/api/sequencing/analyses", headers=tok2).json())
    assert client.delete(f"/api/sequencing/analyses/{aid2}", headers=tok2).status_code == 200


def test_analysis_persists_across_restart(client):
    """database 模式：分析记录落库——清空内存缓存（模拟重启）后详情/峰图/
    列表仍可读，属主过滤在数据库路径同样生效"""
    from fastapi.testclient import TestClient  # noqa
    import app.routes.sequencing_routes as seq_routes

    ua = _ensure_user("persist-a@test.com")
    ub = _ensure_user("persist-b@test.com")
    ha = _login_header(client, ua.email)
    hb = _login_header(client, ub.email)

    ref = ("ref.fasta", b">ref\n" + b"AAG" * 40, "text/plain")
    blob = make_ab1("AAG" * 40, [40] * 120)
    r = client.post("/api/sequencing/analyze",
                    files={"reference": ref, "reads": ("r1.ab1", blob, "application/octet-stream")},
                    headers=ha)
    assert r.status_code == 200, r.text
    aid = r.json()["analysis_id"]

    # 模拟重启：清空内存缓存
    seq_routes._ANALYSES.clear()

    # 详情从数据库回灌，结论/reads 完整
    got = client.get(f"/api/sequencing/analyses/{aid}", headers=ha)
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["reads"] and data["consensus"]["sequence"]
    # 峰图从数据库解压回读（int 键还原）
    trace = client.get(f"/api/sequencing/analyses/{aid}/trace/0", headers=ha).json()
    assert set(trace["channels"].keys()) == {"A", "T", "G", "C"}
    # 列表（数据库路径）可见；属主过滤：B 看不到
    assert any(x["analysis_id"] == aid
               for x in client.get("/api/sequencing/analyses", headers=ha).json())
    assert all(x["analysis_id"] != aid
               for x in client.get("/api/sequencing/analyses", headers=hb).json())
    assert client.get(f"/api/sequencing/analyses/{aid}", headers=hb).status_code == 403

    # 删除连数据库一起清
    assert client.delete(f"/api/sequencing/analyses/{aid}", headers=ha).status_code == 200
    seq_routes._ANALYSES.clear()
    assert client.get(f"/api/sequencing/analyses/{aid}", headers=ha).status_code == 404


def test_export_consensus_carries_poly_verdict(client):
    """共识序列导出带 verdict 注释：GenBank misc_feature 标注判读状态，
    FASTA 头带 poly_verified/poly_unverified 摘要"""
    import app.routes.sequencing_routes as seq_routes
    ref = ">ref\n" + "AAG" * 40
    blob = make_ab1("AAG" * 40, [40] * 120)
    r = client.post("/api/sequencing/analyze",
                    files={"reference": ("ref.fasta", ref, "text/plain"),
                           "reads": ("r1.ab1", blob, "application/octet-stream")})
    assert r.status_code == 200, r.text
    aid = r.json()["analysis_id"]
    tok = _anon_token_header(r)
    rec = seq_routes._ANALYSES[aid]
    rec["homopolymers"] = [
        {"tier": "poly", "base": "A", "start": 30, "end": 50,
         "run_verdict": "accepted", "observed_repeat_count": 21,
         "joint_coverage": True},
        {"tier": "poly", "base": "T", "start": 70, "end": 95,
         "run_verdict": "undetermined"},
    ]
    gb = client.get(f"/api/sequencing/analyses/{aid}/consensus/export?format=genbank",
                    headers=tok)
    assert gb.status_code == 200
    assert "misc_feature    30..50" in gb.text
    assert "重复数确证 21 个" in gb.text and "联合覆盖拼接" in gb.text
    assert "不可判定" in gb.text
    fa = client.get(f"/api/sequencing/analyses/{aid}/consensus/export?format=fasta",
                    headers=tok)
    assert fa.status_code == 200
    assert "poly_unverified=70-95" in fa.text
    # 清理
    client.delete(f"/api/sequencing/analyses/{aid}", headers=tok)


def test_db_retention_prunes_oldest(client, monkeypatch):
    """保留上限：SEQUENCING_DB_MAX_RECORDS 超出后按创建时间淘汰最旧记录"""
    from app import sequencing_store as store
    import app.routes.sequencing_routes as seq_routes
    monkeypatch.setattr(store, "MAX_DB_RECORDS", 2)

    def _mk(name):
        ref = ">ref\n" + "AAG" * 40
        blob = make_ab1("AAG" * 40, [40] * 120)
        r = client.post("/api/sequencing/analyze",
                        files={"reference": ("ref.fasta", ref, "text/plain"),
                               "reads": (name, blob, "application/octet-stream")})
        assert r.status_code == 200
        aid = r.json()["analysis_id"]
        # 本用例只验证「按创建时间淘汰最旧」：清掉 token 模拟改造前的
        # 遗留记录后落库，淘汰结果直接查库观察（匿名记录不进列表）
        seq_routes._ANALYSES[aid]["access_token"] = None
        store.persist_record(seq_routes._ANALYSES[aid])
        return aid

    a1, a2, a3 = _mk("r1.ab1"), _mk("r2.ab1"), _mk("r3.ab1")
    # 内存缓存清掉，只看数据库（终审 B-02 后匿名记录不再进列表端点，
    # 淘汰效果直接查库更准确）
    seq_routes._ANALYSES.clear()
    assert store.load_record(a1) is None
    assert store.load_record(a2) is not None
    assert store.load_record(a3) is not None
    for aid in (a2, a3):
        client.delete(f"/api/sequencing/analyses/{aid}")


def test_export_consensus_covered_only(client):
    """covered_only 导出只取实测覆盖区（部分测序是常规策略）：
    FASTA 每段连续覆盖一条记录、参考坐标入头；GenBank 全长保留、
    未测位置 N 屏蔽并注记实测段；默认不带参数仍是全量导出"""
    ref = ">ref\n" + "AAG" * 40  # 120bp
    blob = make_ab1("AAG" * 20, [40] * 60)  # 只测前 60bp
    r = client.post("/api/sequencing/analyze",
                    files={"reference": ("ref.fasta", ref, "text/plain"),
                           "reads": ("r1.ab1", blob, "application/octet-stream")})
    assert r.status_code == 200, r.text
    aid = r.json()["analysis_id"]
    tok = _anon_token_header(r)
    fa = client.get(
        f"/api/sequencing/analyses/{aid}/consensus/export?format=fasta&covered_only=true",
        headers=tok)
    assert fa.status_code == 200
    assert "ref_pos=1-60" in fa.text and "region=1/1" in fa.text
    seq_lines = [ln for ln in fa.text.splitlines() if not ln.startswith(">")]
    assert "".join(seq_lines) == "AAG" * 20  # 只有覆盖段，无参考填充
    gb = client.get(
        f"/api/sequencing/analyses/{aid}/consensus/export?format=genbank&covered_only=true",
        headers=tok)
    assert gb.status_code == 200
    assert "实测覆盖区 1-60" in gb.text and "N-masked" in gb.text
    assert " 120 bp" in gb.text  # 全长保留（坐标不位移）
    body = gb.text.split("ORIGIN")[1]
    assert body.count("N") >= 60  # 未测位置全部 N
    fa_full = client.get(
        f"/api/sequencing/analyses/{aid}/consensus/export?format=fasta", headers=tok)
    assert "ref_pos=" not in fa_full.text  # 默认全量口径不变
    client.delete(f"/api/sequencing/analyses/{aid}", headers=tok)
