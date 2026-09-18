"""批量测序分析端点测试（POST /api/sequencing/analyze-batch，独立于单样品入口）

覆盖：信息表归组端到端（分析+注册进历史）、无信息表按图谱名归组（含 MX/MX2
前缀歧义的最长包含判定）、缺参考/缺 reads/分析失败等状态、非法信息表 400；
克隆模式（信息表带「克隆号」列）：一个质粒多个克隆每行独立分析、两段式
质粒名部分匹配、歧义不猜、克隆号+引物组合/分隔符变体/克隆号前缀兜底。
"""

import io
import json
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


def test_batch_rejects_excel_lock_file_by_name(client):
    """Excel 打开信息表时留下的 ~$ 锁文件应被明确拒绝（而非 500 或含糊报错）"""
    resp = _post(client, [_ab1("T1.ab1", REF_MX)],
                 excel_part=("~$测序.xlsx", b"OLE2-lock-bytes", "application/octet-stream"))
    assert resp.status_code == 400
    assert "临时文件" in resp.json()["detail"]


def test_batch_rejects_corrupted_excel_bytes(client):
    """非 zip 的垃圾字节（如被占用的锁文件改了名）应得到可读的 400 而非 500"""
    resp = _post(client, [_ab1("T1.ab1", REF_MX)],
                 excel_part=("测序.xlsx", b"not-a-zip-at-all", "application/octet-stream"))
    assert resp.status_code == 400
    assert ".xlsx" in resp.json()["detail"]


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


def test_batch_clone_mode_mixed_name_forms_share_reference(client):
    """同一质粒的克隆行混用全名与单段名（'17648'/'MBYSTC'/'17648 MBYSTC'）
    → 都识别为同一张图谱并共享（真实交付表的写法）"""
    resp = _post(client, [
        _fasta("17648 MBYSTC.fasta", REF_MX),
        _ab1("S99678-M13F-75.ab1", REF_MX),
        _ab1("S99679-M13F-75.ab1", REF_MX),
        _ab1("S99680-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([
        ("S99678", "17648", ["M13F-75"]),
        ("S99679", "MBYSTC", ["M13F-75"]),
        ("S99680", "17648 MBYSTC", ["M13F-75"]),
    ]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["clone_mode"] is True
    assert [it["plasmid"] for it in data["items"]] == ["17648", "MBYSTC", "17648 MBYSTC"]
    assert all(it["status"] == "analyzed" for it in data["items"])
    assert all(it["reference_name"] == "17648 MBYSTC.fasta" for it in data["items"])
    assert data["unmatched"] == []


def test_batch_clone_mode_reference_named_by_one_segment(client):
    """图谱只写一段（'17648.fasta'）：写全名/该段的行直接命中或兜底共享，
    只写另一段（'MBYSTC'）的行经表内别名（全名行）共享同一图谱"""
    resp = _post(client, [
        _fasta("17648.fasta", REF_MX),
        _ab1("S99678-M13F-75.ab1", REF_MX),
        _ab1("S99679-M13F-75.ab1", REF_MX),
        _ab1("S99680-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([
        ("S99678", "17648 MBYSTC", ["M13F-75"]),
        ("S99679", "MBYSTC", ["M13F-75"]),
        ("S99680", "17648", ["M13F-75"]),
    ]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert all(it["status"] == "analyzed" for it in data["items"])
    assert all(it["reference_name"] == "17648.fasta" for it in data["items"])
    assert data["unmatched"] == []


def test_batch_clone_mode_reference_named_by_other_segment(client):
    """对称方向：图谱叫 'MBYSTC.fasta'，只写编号（'17648'）的行经表内别名共享"""
    resp = _post(client, [
        _fasta("MBYSTC.fasta", REF_MX),
        _ab1("S99678-M13F-75.ab1", REF_MX),
        _ab1("S99679-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([
        ("S99678", "17648 MBYSTC", ["M13F-75"]),
        ("S99679", "17648", ["M13F-75"]),
    ]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert all(it["status"] == "analyzed" for it in data["items"])
    assert all(it["reference_name"] == "MBYSTC.fasta" for it in data["items"])
    assert data["unmatched"] == []


def test_batch_clone_mode_segment_map_without_link_stays_unmatched(client):
    """图谱叫 '17648' 而表中只有 'MBYSTC' 行（无全名行建立对应）→ 缺图谱不猜"""
    resp = _post(client, [
        _fasta("17648.fasta", REF_MX),
        _ab1("S1-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([("S1", "MBYSTC", ["M13F-75"])]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"][0]["status"] == "no_reference"
    assert {u["filename"] for u in data["unmatched"]} == {"17648.fasta"}


def test_batch_clone_mode_alias_ambiguous_not_guessed(client):
    """'MBYSTC' 同时是 '17648 MBYSTC'/'17649 MBYSTC' 的组成段 → 别名歧义不猜；
    图谱 '17648' 仍归唯一能命中的 '17648 MBYSTC' 行"""
    resp = _post(client, [
        _fasta("17648.fasta", REF_MX),
        _ab1("S1-M13F-75.ab1", REF_MX),
        _ab1("S2-M13F-75.ab1", REF_MX),
        _ab1("S3-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([
        ("S1", "17648 MBYSTC", ["M13F-75"]),
        ("S2", "17649 MBYSTC", ["M13F-75"]),
        ("S3", "MBYSTC", ["M13F-75"]),
    ]))
    assert resp.status_code == 200, resp.text
    items = {it["plasmid"]: it for it in resp.json()["items"]}
    assert items["17648 MBYSTC"]["status"] == "analyzed"
    assert items["17649 MBYSTC"]["status"] == "no_reference"
    assert items["MBYSTC"]["status"] == "no_reference"


def test_batch_clone_mode_single_part_name_ambiguous_still_refused(client):
    """单段名 'AB2C' 若唯一候选不存在（两张图都含该段）→ 维持缺图谱，不猜"""
    resp = _post(client, [
        _fasta("123-1 AB2C.fasta", REF_MX),
        _fasta("123-2 AB2C.fasta", REF_MX2),
        _ab1("S1-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([("S1", "123-1 AB2C", ["M13F-75"])]))
    assert resp.status_code == 200
    # 全名精确命中的不受影响；再验证只写一段且两张图都包含 → 缺图谱
    resp2 = _post(client, [
        _fasta("123-1 AB2C.fasta", REF_MX),
        _fasta("123-2 AB2C.fasta", REF_MX2),
        _ab1("S2-M13F-75.ab1", REF_MX),
    ], excel_part=_xlsx_clone([("S2", "AB2C", ["M13F-75"])]))
    assert resp2.json()["items"][0]["status"] == "no_reference"


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


# ---------------------------------------------------------------- 整理包与记录时效


def _open_zip(resp):
    import zipfile
    return zipfile.ZipFile(io.BytesIO(resp.content))


def test_batch_report_zip_archives_and_backfills_excel(client):
    """整理包：同一质粒一个文件夹（图谱在根），文件与报告按结论归入 正确/错误
    子文件夹（空桶也保留）+ 整理清单 + 结论回填信息表"""
    resp = _post(client, [
        _fasta("MX.fasta", REF_MX),
        _ab1("T1.ab1", REF_MX),
    ], excel_part=_xlsx([("MX", ["T1"])]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["report_ready"] is True and data["batch_id"]

    got = client.get(f"/api/sequencing/batches/{data['batch_id']}/report")
    assert got.status_code == 200
    assert got.headers["content-type"] == "application/zip"
    zf = _open_zip(got)
    names = zf.namelist()
    assert "测序整理/MX/MX.fasta" in names
    assert "测序整理/MX/正确/T1.ab1" in names
    assert "测序整理/MX/正确/测序分析报告.md" in names
    assert "测序整理/MX/正确/分析结果.json" in names
    assert "测序整理/MX/错误/" in names            # 空桶也保留，结构可预期
    assert "测序整理/整理清单.csv" in names
    assert "测序整理/批次总览.txt" in names
    # 报告含结论；整理清单含 MD5；回填信息表结论与接口返回一致，且留原始备份
    report = zf.read("测序整理/MX/正确/测序分析报告.md").decode("utf-8")
    assert "# MX 测序分析报告" in report and f"> **{data['items'][0]['conclusion']}**" in report
    manifest = zf.read("测序整理/整理清单.csv").decode("utf-8")
    assert "T1.ab1" in manifest and "参考图谱" in manifest
    wb = openpyxl.load_workbook(io.BytesIO(zf.read("测序整理/测序.xlsx")))
    cell = wb.worksheets[0].cell(row=2, column=3).value
    assert cell == data["items"][0]["conclusion"]
    assert "测序整理/原始备份_测序.xlsx" in names


def test_batch_report_clone_mode_backfills_per_clone(client):
    """克隆模式整理包：同一质粒一个文件夹、各克隆文件按结论分入 正确/错误，
    同桶多克隆的报告带克隆号后缀；结论按（质粒, 克隆）回填到对应行"""
    resp = _post(client, [
        _fasta("MX.fasta", REF_MX),
        _ab1("S1-T1.ab1", REF_MX),
        _ab1("S2-T2.ab1", REF_MX),
    ], excel_part=_xlsx_clone([("S1", "MX", ["T1"]), ("S2", "MX", ["T2"])]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["clone_mode"] is True
    zf = _open_zip(client.get(f"/api/sequencing/batches/{data['batch_id']}/report"))
    names = zf.namelist()
    assert "测序整理/MX/正确/S1-T1.ab1" in names
    assert "测序整理/MX/正确/S2-T2.ab1" in names
    assert "测序整理/MX/正确/测序分析报告-S1.md" in names
    assert "测序整理/MX/正确/测序分析报告-S2.md" in names
    wb = openpyxl.load_workbook(io.BytesIO(zf.read("测序整理/测序.xlsx")))
    ws = wb.worksheets[0]
    # 表头 [克隆号, 质粒名称, 测序引物, 测序结果] → 结果列第 4 列；按行核对克隆结论
    assert ws.cell(row=2, column=4).value == data["items"][0]["conclusion"]
    assert ws.cell(row=3, column=4).value == data["items"][1]["conclusion"]


def _origin_block(seq: str) -> str:
    lines = []
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60]
        groups = " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10))
        lines.append(f"{i + 1:>9} {groups}")
    return "\n".join(lines)


def _gb_mx() -> bytes:
    """整序列为 CDS 的 GenBank 图谱（错义突变才能判 不合格）"""
    return (
        f"LOCUS       17648        {len(REF_MX)} bp    DNA     circular SYN 01-JAN-2026\n"
        "DEFINITION  test construct.\n"
        "ACCESSION   17648\n"
        "FEATURES             Location/Qualifiers\n"
        f"     source          1..{len(REF_MX)}\n"
        f"     CDS             1..{len(REF_MX)}\n"
        "                     /label=\"MX\"\n"
        f"ORIGIN\n{_origin_block(REF_MX)}\n//\n"
    ).encode()


def test_batch_report_merges_alias_writings_and_splits_by_conclusion(client):
    """同一质粒的不同写法（'17648'/'17648 MBYSTC'）并档到一个质粒文件夹：
    图谱在文件夹根，各组文件按结论分入 正确/错误 子文件夹"""
    mut = REF_MX[:55] + "C" + REF_MX[56:]   # 密码子 19 ATG→ACG 错义（中段，避开末端修剪）
    gb = _gb_mx()
    resp = _post(client, [
        ("17648.gb", gb, "application/octet-stream"),
        ("17648 MBYSTC.gb", gb, "application/octet-stream"),
        _ab1("T1.ab1", REF_MX),
        _ab1("T2.ab1", mut),
    ], excel_part=_xlsx([("17648", ["T1"]), ("17648 MBYSTC", ["T2"])]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["items"][0]["conclusion"].startswith("合格")
    assert data["items"][1]["conclusion"].startswith("不合格")
    zf = _open_zip(client.get(f"/api/sequencing/batches/{data['batch_id']}/report"))
    names = zf.namelist()
    assert not any(n.startswith("测序整理/17648/") for n in names)   # 两种写法已并档
    assert "测序整理/17648 MBYSTC/17648.gb" in names                 # 图谱在质粒文件夹根
    assert "测序整理/17648 MBYSTC/正确/T1.ab1" in names
    assert "测序整理/17648 MBYSTC/错误/T2.ab1" in names
    assert "测序整理/17648 MBYSTC/正确/测序分析报告.md" in names
    assert "测序整理/17648 MBYSTC/错误/测序分析报告.md" in names
    manifest = zf.read("测序整理/整理清单.csv").decode("utf-8")
    assert "结论" in manifest and "T2.ab1" in manifest and "不合格" in manifest


def test_batch_report_includes_unmatched_files(client):
    """未匹配文件不参与分析，但归档进 未匹配文件/ 并记入整理清单"""
    resp = _post(client, [
        _fasta("MX.fasta", REF_MX),
        _ab1("T1.ab1", REF_MX),
        _ab1("孤儿.ab1", REF_MX),
    ], excel_part=_xlsx([("MX", ["T1"])]))
    assert resp.status_code == 200
    zf = _open_zip(client.get(f"/api/sequencing/batches/{resp.json()['batch_id']}/report"))
    assert "测序整理/未匹配文件/孤儿.ab1" in zf.namelist()


def test_batch_report_skipped_when_over_cache_cap(client, monkeypatch):
    """体积超整理包缓存上限：正常返回分析结果，但不提供整理包下载"""
    from app.routes import sequencing_routes as sr
    monkeypatch.setattr(sr, "MAX_BATCH_CACHE_BYTES", 1)
    resp = _post(client, [_fasta("MX.fasta", REF_MX), _ab1("T1.ab1", REF_MX)],
                 excel_part=_xlsx([("MX", ["T1"])]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_ready"] is False
    got = client.get(f"/api/sequencing/batches/{data['batch_id']}/report")
    assert got.status_code == 404
    assert "过期" in got.json()["detail"] or "不存在" in got.json()["detail"]


def test_analyses_and_batch_expire_after_ttl(client):
    """分析记录与整理包 15 分钟 TTL：把记录时间戳拨旧 → 列表/详情/整理包全部消失"""
    from app.routes import sequencing_routes as sr
    resp = _post(client, [_fasta("MX.fasta", REF_MX), _ab1("T1.ab1", REF_MX)],
                 excel_part=_xlsx([("MX", ["T1"])]))
    data = resp.json()
    aid = data["items"][0]["analysis_id"]
    assert sr._ANALYSES.get(aid) is not None
    assert client.get(f"/api/sequencing/batches/{data['batch_id']}/report").status_code == 200

    for rec in sr._ANALYSES.values():
        rec["_created_ts"] -= sr.ANALYSIS_TTL + 10
    sr._BATCHES[data["batch_id"]]["created_ts"] -= sr.BATCH_TTL + 10

    assert client.get("/api/sequencing/analyses").json() == []
    assert client.get(f"/api/sequencing/analyses/{aid}").status_code == 404
    got = client.get(f"/api/sequencing/batches/{data['batch_id']}/report")
    assert got.status_code == 404 and "过期" in got.json()["detail"]
