"""批量测序分析端点测试（POST /api/sequencing/analyze-batch，独立于单样品入口）

覆盖：信息表归组端到端（分析+注册进历史）、无信息表按图谱名归组（含 MX/MX2
前缀歧义的最长包含判定）、缺参考/缺 reads/分析失败等状态、非法信息表 400；
克隆模式（信息表带「克隆号」列）：一个质粒多个克隆每行独立分析、两段式
质粒名部分匹配、歧义不猜、克隆号+引物组合/分隔符变体/克隆号前缀兜底。
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
from core.sanger.batch import load_excel, match_clone_files, norm_stem  # noqa: E402

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


def _xlsx_clone(rows) -> tuple:
    """克隆模式信息表：中英文分号混用分隔引物（真实交付常见）"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["克隆号", "质粒名称", "测序引物", "测序结果"])
    for i, (clone, name, primers) in enumerate(rows):
        sep = "；" if i % 2 == 0 else ";"
        ws.append([clone, name, sep.join(primers), None])
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


# ---------------------------------------------------------------- 克隆模式


def test_batch_clone_mode_analyzes_each_clone(client):
    """信息表带克隆号列：一个质粒的每个克隆独立成组、独立分析并注册"""
    resp = _post(client, [
        _fasta("123-1 AB2C.fasta", REF_MX),
        _ab1("S99678-M13F-75.ab1", REF_MX),
        _ab1("S99678-M13R-88.ab1", REF_MX),
        _ab1("S99679-M13F-75.ab1", REF_MX),
        _ab1("S99999-M13F-75.ab1", REF_MX),   # 表中不存在的克隆 → 未匹配
    ], excel_part=_xlsx_clone([
        ("S99678", "123-1 AB2C", ["M13F-75", "M13R-88"]),
        ("S99679", "123-1 AB2C", ["M13F-75", "M13R-88"]),
        ("S99681", "123-1 AB2C", ["M13F-75", "M13R-88"]),
    ]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["excel_mode"] is True and data["clone_mode"] is True
    items = data["items"]
    assert [(it["plasmid"], it["clone"]) for it in items] == [
        ("123-1 AB2C", "S99678"), ("123-1 AB2C", "S99679"), ("123-1 AB2C", "S99681")]
    assert items[0]["status"] == "analyzed" and items[0]["read_count"] == 2
    assert items[1]["status"] == "analyzed" and items[1]["read_count"] == 1
    assert items[2]["status"] == "no_reads"
    assert "S99681" in items[2]["conclusion"]
    # 每个克隆一条独立分析记录，sample_name = 克隆号 + 质粒名
    assert items[0]["analysis_id"] != items[1]["analysis_id"]
    got = client.get(f"/api/sequencing/analyses/{items[0]['analysis_id']}").json()
    assert got["sample_name"] == "S99678 123-1 AB2C"
    assert {u["filename"] for u in data["unmatched"]} == {"S99999-M13F-75.ab1"}


def test_batch_clone_mode_matches_partial_plasmid_name(client):
    """两段式质粒名只写一段（'AB2C'）也能匹配图谱 '123-1 AB2C.fasta'"""
    resp = _post(client, [
        _fasta("123-1 AB2C.fasta", REF_MX),
        _ab1("S1-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([("S1", "AB2C", ["M13F-75"])]))
    assert resp.status_code == 200
    it = resp.json()["items"][0]
    assert it["status"] == "analyzed"
    assert it["reference_name"] == "123-1 AB2C.fasta"


def test_batch_clone_mode_ambiguous_partial_name_not_guessed(client):
    """部分名 'AB2C' 同时命中 '123-1 AB2C'/'123-2 AB2C' 两张图 → 不猜，判未匹配"""
    resp = _post(client, [
        _fasta("123-1 AB2C.fasta", REF_MX),
        _fasta("123-2 AB2C.fasta", REF_MX2),
        _ab1("S1-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([("S1", "AB2C", ["M13F-75"])]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"][0]["status"] == "no_reference"
    assert len(data["unmatched"]) == 2
    assert all("无法唯一确定" in u["reason"] for u in data["unmatched"])


def test_load_excel_clone_column_and_numeric_cells():
    """克隆号列识别；数值单元格 99678.0 → '99678'；中英文分号均分隔"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["克隆号", "质粒名称", "测序引物"])
    ws.append([99678, "P1", "M13F-75；M13R-88"])
    ws.append([99679.0, "P1", "M13F-75;M13R-88"])
    buf = io.BytesIO()
    wb.save(buf)
    _, _, cols, rows = load_excel(buf.getvalue())
    assert "clone" in cols
    assert [r["clone"] for r in rows] == ["99678", "99679"]
    assert all(r["primers"] == ["M13F-75", "M13R-88"] for r in rows)


def test_match_clone_files_fallbacks_and_conflicts():
    """分隔符变体/包含/克隆号前缀兜底 与 冲突/未知克隆拒绝"""

    def mk(name, ext=".fasta"):
        return {"ext": ext, "stem": norm_stem(name.rsplit(".", 1)[0]), "name": name, "bytes": b""}

    # 分隔符变体文件名（下划线）与复制版后缀都能命中组合
    rows = [{"name": "P", "clone": "C1", "primers": ["M13F-75"]}]
    groups, unmatched = match_clone_files(
        rows, [mk("C1_M13F-75.ab1", ".ab1"), mk("C1-M13F-75-2.ab1", ".ab1")])
    assert len(groups[0]["reads"]) == 2 and not unmatched

    # 信息表漏填引物 → 克隆号前缀兜底；未知克隆 S996789-x 不被吸收
    rows = [{"name": "P", "clone": "S99678", "primers": []}]
    groups, unmatched = match_clone_files(
        rows, [mk("S99678.ab1", ".ab1"), mk("S99678-extra.ab1", ".ab1"), mk("S996789-x.ab1", ".ab1")])
    assert len(groups[0]["reads"]) == 2 and len(unmatched) == 1

    # 裸引物名出现在多行 → 无法归属；未知克隆号的文件不被吸收
    rows = [{"name": "P", "clone": "C1", "primers": ["M13F-75"]},
            {"name": "P", "clone": "C2", "primers": ["M13F-75"]}]
    groups, unmatched = match_clone_files(rows, [mk("M13F-75.ab1", ".ab1")])
    assert groups[0]["reads"] == [] and "多个克隆行" in unmatched[0]["reason"]
    groups, unmatched = match_clone_files(rows, [mk("S99999-M13F-75.ab1", ".ab1")])
    assert groups[0]["reads"] == [] and len(unmatched) == 1

    # 同一组合对应两个质粒（克隆号重复）→ 拒绝归属
    rows = [{"name": "P1", "clone": "C1", "primers": ["M13F-75"]},
            {"name": "P2", "clone": "C1", "primers": ["M13F-75"]}]
    groups, unmatched = match_clone_files(rows, [mk("C1-M13F-75.ab1", ".ab1")])
    assert sum(len(g["reads"]) for g in groups) == 0
    assert "对应多行" in unmatched[0]["reason"]
