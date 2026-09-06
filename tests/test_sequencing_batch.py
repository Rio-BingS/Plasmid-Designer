"""批量测序分析端点测试（POST /api/sequencing/analyze-batch，独立于单样品入口）

覆盖：信息表归组端到端（分析+注册进历史）、无信息表按图谱名归组（含 MX/MX2
前缀歧义的最长包含判定）、缺参考/缺 reads/分析失败等状态、非法信息表 400。
"""

import io
import os
import sys

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from abif_utils import make_ab1  # noqa: E402

REF_MX = "ATGAAACGT" * 12 + "TAA"          # 123 bp
REF_MX2 = "ATGCCCGGT" * 12 + "TAA"         # 与 REF_MX 前缀歧义（mx 是 mx2 的前缀）


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _fasta(name: str, seq: str) -> tuple:
    return (name, f">{name}\n{seq}\n".encode(), "application/octet-stream")


def _ab1(name: str, seq: str) -> tuple:
    return (name, make_ab1(seq, [40] * len(seq)), "application/octet-stream")


def _xlsx(rows) -> tuple:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    for name, primers in rows:
        ws.append([name, ";".join(primers), None])
    buf = io.BytesIO()
    wb.save(buf)
    return ("测序.xlsx", buf.getvalue(), "application/octet-stream")


def _post(client, file_parts, excel_part=None, min_q="20"):
    files = [("files", fp) for fp in file_parts]
    if excel_part:
        files.append(("excel", excel_part))
    return client.post("/api/sequencing/analyze-batch", files=files, data={"min_q": min_q})


def test_batch_with_excel_analyzes_each_plasmid(client):
    """信息表模式：按引物列/质粒名归组，逐质粒分析并注册进标准分析记录"""
    resp = _post(client, [
        _fasta("MX.fasta", REF_MX),
        _ab1("T1.ab1", REF_MX),
        _fasta("P2.fasta", REF_MX2),
        _ab1("T2.ab1", REF_MX2),
        _ab1("孤儿.ab1", REF_MX),      # 不在任何质粒的引物列 → 未匹配
        ("说明.txt", b"readme", "application/octet-stream"),  # 无关文件 → ignored
    ], excel_part=_xlsx([("MX", ["T1"]), ("P2", ["T2"])]))
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["excel_mode"] is True
    assert [it["plasmid"] for it in data["items"]] == ["MX", "P2"]
    for it in data["items"]:
        assert it["status"] == "analyzed"
        assert it["conclusion"] == "合格：与设计一致"
        assert it["analysis_id"] and it["read_count"] == 1
        assert it["variant_count"] == 0 and it["coverage_percent"] == 100.0
        assert it["reference_length"] == len(REF_MX)

    # 成功分析已注册为标准记录：详情/历史端点可用
    got = client.get(f"/api/sequencing/analyses/{data['items'][0]['analysis_id']}").json()
    assert got["sample_name"] == "MX"
    listing = client.get("/api/sequencing/analyses").json()
    assert any(h["analysis_id"] == data["items"][1]["analysis_id"] for h in listing)

    assert {u["filename"] for u in data["unmatched"]} == {"孤儿.ab1"}
    assert data["ignored_files"] == ["说明.txt"]


def test_batch_without_excel_groups_by_reference_name(client):
    """无信息表：按图谱名包含关系归组；MX2 命中 MX/MX2 时取最长包含"""
    resp = _post(client, [
        _fasta("MX.fasta", REF_MX),
        _fasta("MX2.fasta", REF_MX2),
        _ab1("MX-T1.ab1", REF_MX),
        _ab1("MX2-T1.ab1", REF_MX2),
    ])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["excel_mode"] is False
    by_name = {it["plasmid"]: it for it in data["items"]}
    assert set(by_name) == {"MX", "MX2"}
    assert by_name["MX"]["read_count"] == 1 and by_name["MX2"]["read_count"] == 1
    assert all(it["status"] == "analyzed" for it in data["items"])
    assert data["unmatched"] == []


def test_batch_no_excel_ambiguous_read_goes_unmatched(client):
    """read 同时等长包含两个图谱名时无法唯一归组 → 未匹配"""
    resp = _post(client, [
        _fasta("MXA.fasta", REF_MX),
        _fasta("MXB.fasta", REF_MX2),
        _ab1("MXA-MXB-T1.ab1", REF_MX),
    ])
    assert resp.status_code == 200
    data = resp.json()
    assert {it["status"] for it in data["items"]} == {"no_reads"}
    assert len(data["unmatched"]) == 1
    assert "无法唯一归组" in data["unmatched"][0]["reason"]


def test_batch_reports_missing_reference_and_reads(client):
    """缺图谱 → no_reference（与脚本同一句话）；只有图谱 → no_reads"""
    resp = _post(client, [
        _fasta("P2.fasta", REF_MX2),
        _ab1("X1.ab1", REF_MX),
        _ab1("X2.ab1", REF_MX),
    ], excel_part=_xlsx([("X", ["X1", "X2"]), ("P2", [])]))
    assert resp.status_code == 200
    data = resp.json()
    by_name = {it["plasmid"]: it for it in data["items"]}
    assert by_name["X"]["status"] == "no_reference"
    assert "缺少同名参考图谱" in by_name["X"]["conclusion"]
    assert by_name["X"]["read_count"] == 2
    assert by_name["P2"]["status"] == "no_reads"
    assert "未匹配到它的测序文件" in by_name["P2"]["conclusion"]


def test_batch_failed_plasmid_does_not_block_others(client):
    """单个质粒参考序列过短 → failed，其余照常分析"""
    short = "ATGAAACGTAA"  # 11 bp < 50
    resp = _post(client, [
        _fasta("BAD.fasta", short),
        _ab1("B1.ab1", short),
        _fasta("GOOD.fasta", REF_MX),
        _ab1("G1.ab1", REF_MX),
    ], excel_part=_xlsx([("BAD", ["B1"]), ("GOOD", ["G1"])]))
    assert resp.status_code == 200
    by_name = {it["plasmid"]: it for it in resp.json()["items"]}
    assert by_name["BAD"]["status"] == "failed"
    assert by_name["BAD"]["conclusion"].startswith("分析失败：")
    assert by_name["BAD"]["analysis_id"] is None
    assert by_name["GOOD"]["status"] == "analyzed"


def test_batch_rejects_excel_without_plasmid_header(client):
    wb = openpyxl.Workbook()
    wb.active.append(["sample", "primer"])
    buf = io.BytesIO()
    wb.save(buf)
    resp = _post(client, [_ab1("T1.ab1", REF_MX)],
                 excel_part=("bad.xlsx", buf.getvalue(), "application/octet-stream"))
    assert resp.status_code == 400
    assert "质粒" in resp.json()["detail"]


def test_batch_rejects_when_no_usable_files(client):
    resp = _post(client, [("说明.txt", b"x", "application/octet-stream")])
    assert resp.status_code == 400
    assert "未找到" in resp.json()["detail"]
