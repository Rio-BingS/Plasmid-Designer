"""测序分析路由集成测试（design 全链路 + 内存结果端点）"""

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
