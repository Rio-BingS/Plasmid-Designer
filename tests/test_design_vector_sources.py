"""设计页一次性载体：本地上传 / 在线导入（只取不存）

背景（缺陷）：设计页只能从载体库选载体，而载体库是共享库、写入口只对管理员
开放（/api/vectors/import/* 全部依赖 get_admin_user）。普通用户手里的
.dna/.gb 或在线来源的质粒完全无法参与设计。

这里的用例锁住四件事：
1. 上传/在线导入返回的载荷能直接作为 POST /api/design 的 vector 提交；
2. 该路径**不写载体库**（不产生 YAML、不覆盖同名载体、不需要管理员）；
3. 组装用的是载体真实序列（构造体长度 = 载体 + 插入；推测 MCS 时为纯插入）；
4. 载体库路径（vector_id）行为不变。
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import site_settings
from app.features import ALL_FEATURES

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "backend"))

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"X-Requested-With": "XMLHttpRequest"}


# ==================== 夹具 ====================

@pytest.fixture()
def client(monkeypatch):
    from app.main import app

    s = {"registration_open": True, "email_verification_required": False,
         "anonymous_features": list(ALL_FEATURES), "user_features": list(ALL_FEATURES)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(s)))
    return TestClient(app)


@pytest.fixture()
def isolated_vectors_dir(monkeypatch, tmp_path):
    """把载体库目录指到空临时目录：既能断言「没写盘」，也避免污染真实 data/vectors"""
    from app import config, design_service

    monkeypatch.setattr(config.settings, "VECTORS_DIR", str(tmp_path))
    monkeypatch.setattr(design_service.settings, "VECTORS_DIR", str(tmp_path))
    design_service.invalidate_vector_library_cache()
    yield tmp_path
    design_service.invalidate_vector_library_cache()


# 600 bp 的合成环状载体：足够长（>50 bp）才会被当真实骨架而不是占位骨架
VECTOR_SEQ = (
    "ATCCGGATATAGTTCCTCCTTTCAGCAAAAAACCCCTCAAGACCCGTTTAGAGGCCCCAAGGGGTTAT"
    "GCTAGTTATTGCTCAGCGGTGGCAGCAGCCAACTCAGCTTCCTTTCGGGCTTTGTTAGCAGCCGGATC"
    "TCAGTGGTGGTGGTGGTGGTGCTCGAGTGCGGCCGCAAGCTTGTCGACGGAGCTCGAATTCGGATCCG"
    "CGACCCATTTGCTGTCCACCAGTCATGCTAGCCATATGGCTGCCGCGCGGCACCAGGCCGCTGCTGTG"
    "ATGATGATGATGATGGCTGCTGCCCATGGTATATCTCCTTCTTAAAGTTAAACAAAATTATTTCTAGA"
    "GGGGAATTGTTATCCGCTCACAATTCCCCTATAGTGAGTCGTATTAATTTCGCGGGATCGAGATCTCG"
    "ATCCTCTACGCCGGACGCATCGTGGCCGGCATCACCGGCGCCACAGGTGCGGTTGCTGGCGCCTATAT"
    "CGCCGACATCACCGATGGGGAAGATCGGGCTCGCCACTTCGGGCTCATGAGCGCTTGTTTCGGCGTGG"
    "GTATGGTGGCAGGCCCCGTGGCCGGGGGACTGTTGGGCGCCATCTCCTTGCATGCACCATTCCTTGCG"
    "GCGGCGGTGCTCAACGGCCTCAACCTACTACTGGGCTGCTTCCTAATGCAGGAGTCGCATAAGGGAGA"
)


def _gb(seq: str, features: str = "", topology: str = "circular",
        name: str = "pTEST") -> str:
    lines = [f"LOCUS       {name:<16}{len(seq):>12} bp    DNA     {topology:<8} SYN 01-JAN-2026",
             "DEFINITION  test vector.", "FEATURES             Location/Qualifiers",
             features.rstrip("\n"), "ORIGIN"]
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60].lower()
        lines.append(f"{i + 1:>9} " + " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10)))
    lines.append("//")
    return "\n".join(lines) + "\n"


def _insert_payload() -> dict:
    return {
        "sequence": "MKVLWAALLTFLGCAATSGSQAP",
        "sequence_type": "amino_acid",
        "sequence_name": "insert",
        "vector_id": "",
        "cloning_method": "gibson",
        "optimize_codons": True,
        "target_species": "ecoli",
        "gc_min": 40, "gc_max": 60, "homology_arm": 20,
        "enzyme": "BsaI", "include_report": True,
    }


def _inline_vector(seq: str = VECTOR_SEQ, features=None, **kw) -> dict:
    out = {"id": "adhoc_upload_pTEST", "name": "pTEST", "sequence": seq,
           "topology": "circular", "features": features or []}
    out.update(kw)
    return out


# ==================== 本地上传 ====================

def test_upload_returns_usable_payload(client, isolated_vectors_dir):
    body = _gb(VECTOR_SEQ, '     CDS             100..180\n'
                           '                     /label="bla"\n'
                           '     rep_origin      250..400\n'
                           '                     /label="ori"\n')
    r = client.post("/api/design/vector/upload", headers=HEADERS,
                    files={"file": ("pTEST.gb", body.encode(), "text/plain")})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["length"] == len(VECTOR_SEQ)
    assert data["sequence"] == VECTOR_SEQ
    assert data["name"] == "pTEST"
    assert data["id"].startswith("adhoc_")
    names = {f["name"] for f in data["features"]}
    assert {"bla", "ori"} <= names
    assert data["topology"] == "circular"
    # 没有 MCS 注释时给推测结果（含插入点与告警），而不是静默回退
    assert data["mcs"] is not None and data["unique_enzymes"]
    assert any("MCS" in w for w in data["warnings"])


def test_upload_does_not_touch_vector_library(client, isolated_vectors_dir):
    body = _gb(VECTOR_SEQ)
    r = client.post("/api/design/vector/upload", headers=HEADERS,
                    files={"file": ("pTEST.gb", body.encode(), "text/plain")})
    assert r.status_code == 200
    assert list(isolated_vectors_dir.glob("*.yaml")) == []


def test_upload_rejects_raw_unknown_format(client, isolated_vectors_dir):
    r = client.post("/api/design/vector/upload", headers=HEADERS,
                    files={"file": ("notes.txt", b"hello world", "text/plain")})
    assert r.status_code == 415
    assert "不支持" in r.json()["detail"] or "格式" in r.json()["detail"]


def test_upload_rejects_empty_file(client, isolated_vectors_dir):
    r = client.post("/api/design/vector/upload", headers=HEADERS,
                    files={"file": ("empty.gb", b"", "text/plain")})
    assert r.status_code == 422


def test_upload_rejects_oversize(client, isolated_vectors_dir, monkeypatch):
    from app.routes import design_vector_routes as D

    monkeypatch.setattr(D, "MAP_MAX_UPLOAD_BYTES", 1024)
    r = client.post("/api/design/vector/upload", headers=HEADERS,
                    files={"file": ("big.gb", b"A" * 2048, "text/plain")})
    assert r.status_code == 413


# ==================== 用一次性载体做设计 ====================

def test_design_uses_inline_vector_sequence(client, isolated_vectors_dir):
    payload = _insert_payload()
    payload["vector"] = _inline_vector()
    r = client.post("/api/design", json=payload, headers=HEADERS)
    assert r.status_code == 200, r.text
    design_id = r.json()["design_id"]

    res = client.get(f"/api/design/{design_id}", headers=HEADERS).json()
    assert res["status"] == "completed", res.get("errors")
    assert res["vector_name"] == "pTEST"
    assert res["vector_id"].startswith("adhoc_")
    # 构造体 = 载体骨架 + 插入（推测 MCS 是纯插入，不删骨架碱基）
    assert len(res["construct_sequence"]) == len(VECTOR_SEQ) + len(res["optimized_sequence"])
    assert res["final_length"] == len(res["construct_sequence"])
    assert any("MCS" in w for w in res["warnings"])


def test_gibson_design_keeps_mcs_and_does_not_delete_backbone(client, isolated_vectors_dir):
    """Gibson 是「一位点线性化」，不切掉任何碱基——MCS 必须原样留在构建体里

    旧实现三种克隆方法共用「MCS 区间整段替换」，Gibson 也会删掉整段 MCS
    （用户反馈的「图谱上残留/误删」同源）。
    """
    features = [{"name": "MCS", "type": "mcs", "start": 120, "end": 260, "strand": "+"}]
    payload = _insert_payload()
    payload["vector"] = _inline_vector(features=features)
    r = client.post("/api/design", json=payload, headers=HEADERS)
    design_id = r.json()["design_id"]
    res = client.get(f"/api/design/{design_id}", headers=HEADERS).json()
    assert res["status"] == "completed", res.get("errors")
    assert (len(res["construct_sequence"])
            == len(VECTOR_SEQ) + len(res["optimized_sequence"]))
    # 载体序列一个碱基都没少
    seq = res["construct_sequence"]
    head = VECTOR_SEQ[:int(res["insert_start"]) - 1]
    tail = VECTOR_SEQ[int(res["insert_start"]) - 1:]
    assert seq.startswith(head) and seq.endswith(tail)


def test_design_without_inline_vector_still_uses_library(client, isolated_vectors_dir):
    """回归锁：不给 vector 时仍按 vector_id 查载体库（旧行为不变）"""
    from app import design_service

    lib = design_service.get_vector_library()
    assert lib.get_vector("pET-28a") is None  # 临时目录是空的 → 走「未找到载体」回退
    payload = _insert_payload()
    payload["vector_id"] = "pET-28a"
    r = client.post("/api/design", json=payload, headers=HEADERS)
    design_id = r.json()["design_id"]
    res = client.get(f"/api/design/{design_id}", headers=HEADERS).json()
    assert res["status"] == "completed", res.get("errors")
    assert res["vector_id"] == "pET-28a"
    assert any("未找到载体" in w for w in res["warnings"])


def test_inline_vector_rejects_illegal_bases(client, isolated_vectors_dir):
    payload = _insert_payload()
    payload["vector"] = _inline_vector(seq="ACGT" + "ZZZZ")
    r = client.post("/api/design", json=payload, headers=HEADERS)
    assert r.status_code == 422
    assert "非法字符" in r.text


def test_inline_vector_drops_out_of_range_features(client, isolated_vectors_dir):
    features = [{"name": "bad", "type": "CDS", "start": 500, "end": 9000, "strand": "+"}]
    payload = _insert_payload()
    payload["vector"] = _inline_vector(features=features)
    r = client.post("/api/design", json=payload, headers=HEADERS)
    design_id = r.json()["design_id"]
    res = client.get(f"/api/design/{design_id}", headers=HEADERS).json()
    assert res["status"] == "completed", res.get("errors")
    assert any("越界" in w for w in res["warnings"])


# ==================== 一次性载体 → Vector 的组装语义 ====================

def test_inferred_mcs_inserts_without_deleting_backbone():
    from app.adhoc_vector import build_vector
    from app.design_service import assemble_construct

    vector, meta = build_vector(name="pTEST", sequence=VECTOR_SEQ)
    assert meta["mcs"]["origin"] == "inferred"
    asm = assemble_construct(vector, "ATG" * 30, "ins")
    assert len(asm.sequence) == len(VECTOR_SEQ) + 90
    assert asm.insert_start == meta["mcs"]["start"]


def test_mcs_sites_carry_real_overhang_bases():
    """扫描结果里的 overhang 是类型（"5prime"），不能直接当碱基用"""
    from app.adhoc_vector import overhang_bases

    # BamHI (GGATCC) 切在 G^GATCC，突出端是 GATC
    site = {"name": "BamHI", "position": 1, "cut_fwd": 1, "cut_rev": 5,
            "overhang": "5prime", "recognition": "GGATCC"}
    assert overhang_bases("GGATCCAAAA", site) == "GATC"
    # 3' 突出端的酶没有可用的 5' overhang
    pst = {"name": "PstI", "position": 1, "cut_fwd": 5, "cut_rev": 1,
           "overhang": "3prime", "recognition": "CTGCAG"}
    assert overhang_bases("CTGCAGAAAA", pst) == ""


def test_gibson_site_outside_mcs_uses_unique_cut():
    """一次性载体的唯一位点表让「定位酶切位点」在 MCS 之外也能生效"""
    from app.adhoc_vector import build_vector
    from app.design_service import resolve_gibson_insert_pos
    from app.routes.models import DesignRequest

    vector, meta = build_vector(name="pTEST", sequence=VECTOR_SEQ)
    outside = [e for e in meta["unique_enzymes"]]
    assert outside
    enzyme = next(n for n, cuts in vector.unique_site_cuts.items())
    req = DesignRequest(sequence="ATG", sequence_type="dna", vector_id="",
                        cloning_method="gibson", gibson_site=enzyme)
    pos = resolve_gibson_insert_pos(req, vector)
    assert pos == vector.unique_site_cuts[enzyme][0] - 1


def test_gibson_falls_back_to_backbone_middle_without_mcs():
    """没有 MCS/唯一位点时用骨架中点（此前固定 100，对真实载体是任意位置）"""
    from types import SimpleNamespace

    from app.design_service import resolve_gibson_insert_pos
    from app.routes.models import DesignRequest

    vector = SimpleNamespace(sequence="A" * 800, mcs=None, unique_site_cuts={})
    req = DesignRequest(sequence="ATG", sequence_type="dna", vector_id="",
                        cloning_method="gibson")
    assert resolve_gibson_insert_pos(req, vector) == 400


# ==================== 在线导入 ====================

def test_online_ensembl_import_returns_payload(client, isolated_vectors_dir, monkeypatch):
    from core import online_sources as src
    from core.plasmid_map.parsers import ParsedRecord

    def fake_record(identifier, seq_type="cds", *, name=None, max_length=None):
        return ParsedRecord(name="ENSG_TEST", topology="linear", sequence=VECTOR_SEQ,
                            source_format="fasta", features=[])

    monkeypatch.setattr(src, "ensembl_record", fake_record)
    r = client.post("/api/design/vector/online", headers=HEADERS,
                    json={"source": "ensembl", "identifier": "ENSG00000139618",
                          "seq_type": "cds"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["source"] == "ensembl_cds"
    assert data["topology"] == "linear"   # 线性片段不能被当环状
    assert data["length"] == len(VECTOR_SEQ)


def test_online_ncbi_import_returns_payload(client, isolated_vectors_dir, monkeypatch):
    from app.routes import design_vector_routes as D

    body = _gb(VECTOR_SEQ)

    def fake_fetch(acc, *, max_bytes, max_length):
        assert acc == "L09137"
        return body

    monkeypatch.setattr(D, "fetch_genbank_text", fake_fetch)
    r = client.post("/api/design/vector/online", headers=HEADERS,
                    json={"source": "ncbi", "accession": "l09137"})
    assert r.status_code == 200, r.text
    assert r.json()["length"] == len(VECTOR_SEQ)
    assert r.json()["source"] == "ncbi"


def test_online_snapgene_requires_login(client, isolated_vectors_dir, monkeypatch):
    from core import online_sources as src

    monkeypatch.setattr(src, "snapgene_online_enabled", lambda: True)
    r = client.post("/api/design/vector/online", headers=HEADERS,
                    json={"source": "snapgene", "set_id": "s", "plasmid_id": "p"})
    assert r.status_code == 401


def test_online_snapgene_disabled_returns_404(client, isolated_vectors_dir, monkeypatch):
    from core import online_sources as src

    monkeypatch.setattr(src, "snapgene_online_enabled", lambda: False)
    r = client.post("/api/design/vector/online", headers=HEADERS,
                    json={"source": "snapgene", "set_id": "s", "plasmid_id": "p"})
    assert r.status_code == 404


def test_online_addgene_requires_admin(client, isolated_vectors_dir):
    """Addgene 全长序列用部署方配置的会话，与载体库侧一致只允许管理员触发"""
    r = client.post("/api/design/vector/online", headers=HEADERS,
                    json={"source": "addgene", "addgene_id": "12259"})
    assert r.status_code == 401


def test_online_unknown_source_is_422(client, isolated_vectors_dir):
    r = client.post("/api/design/vector/online", headers=HEADERS,
                    json={"source": "bogus"})
    assert r.status_code == 422


# ==================== 请求体上限 ====================

def test_body_limit_for_design_vector_endpoints(monkeypatch):
    """/api/design/vector/* 走流式体积上限：上传按文件上限，其余按 JSON 上限"""
    from app import map_body_limit as M
    from app.routes import models

    monkeypatch.setattr(models, "MAP_MAX_UPLOAD_BYTES", 1000)
    assert M.design_vector_body_limit("/api/design/vector/upload") == 1000 + M.MULTIPART_OVERHEAD
    # 内联载体回传可能带整条载体序列，按 JSON 上限（远大于 1000）
    assert M.design_vector_body_limit("/api/design/vector/online") > 1000


# ==================== 限流档位 ====================

def test_rate_limit_buckets_for_design_vector_endpoints():
    from app.rate_limit import classify_endpoint

    assert classify_endpoint("POST", "/api/design/vector/upload") == "upload"
    assert classify_endpoint("POST", "/api/design/vector/online") == "ncbi"
    assert classify_endpoint("GET", "/api/design/vector/snapgene/search") == "ncbi"
    assert classify_endpoint("GET", "/api/design/vector/sources") == "default"

# ==================== 双酶切实时预览 ====================

_PREVIEW_SEQ = ("C" * 100 + "CTCGAG" + "A" * 100 + "CATATG" + "G" * 100
                + "C" * 100)


def test_design_restriction_preview_returns_construct_layout(client):
    """预览返回的是**成品构建体**的布局：被切走的片段不在图里"""
    body = {
        "enzymes": ["XhoI", "NdeI"],
        "insert_length": 300,
        "vector": {
            "name": "synthetic",
            "sequence": _PREVIEW_SEQ,
            "topology": "circular",
            "features": [
                {"name": "T7 promoter", "type": "promoter", "start": 230,
                 "end": 248, "strand": "-"},
            ],
        },
    }
    r = client.post("/api/design/restriction-preview", headers=HEADERS, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    # 负链启动子 → 基因 5′ 端接右切点（NdeI），成品里基因占 101+5 … +300
    assert data["strand"] == "-"
    assert data["source"] == "annotation"
    assert data["gene_5prime_enzyme"] == "NdeI"
    assert data["gene_3prime_enzyme"] == "XhoI"
    # 切点之间真正被切走的是 107 bp（XhoI 切点 101 → NdeI 切点 208）
    assert data["removed_bp"] == 107
    assert data["gene"]["length"] == 300
    assert {s["role"] for s in data["sites"]} == {"five", "three"}
    # 启动子出现在成品坐标里（示意图要画它的方向）
    promoter = next(f for f in data["features"] if f["name"] == "T7 promoter")
    assert promoter["strand"] == "-"
    assert promoter["start"] > data["gene"]["end"]
    # 构建体 = 载体 - 切除 + (XhoI 残留 5 + 基因 300 + NdeI 残留 2)
    assert data["construct_length"] == len(_PREVIEW_SEQ) - 107 + 300 + 5 + 2


def test_design_restriction_preview_rejects_single_enzyme(client):
    r = client.post("/api/design/restriction-preview", headers=HEADERS, json={
        "enzymes": ["XhoI", "XhoI"],
        "vector": {"name": "synthetic", "sequence": _PREVIEW_SEQ,
                   "topology": "circular", "features": []},
    })
    assert r.status_code == 422


def test_restriction_preview_rate_limit_bucket():
    from app.rate_limit import classify_endpoint

    assert classify_endpoint("POST", "/api/design/restriction-preview") == "default"

