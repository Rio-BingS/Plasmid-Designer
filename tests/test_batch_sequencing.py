"""批量测序整理分析脚本测试（scripts/batch_sequencing_report.py）

覆盖：Excel 解析、文件名匹配（引物列/质粒名/未匹配）、整理落盘（同名异内容
不覆盖）、Excel 结论各分支、"信息表 + 图谱 + .ab1 → 质粒文件夹 + 报告 + 回填"
端到端，离线命令行与网页端点整理包逐项对比（文件集合、报告全文、JSON、
清单、总览、回填 Excel，允许差异显式列出），重跑同一输出目录时的旧副本
清理、清单记录实际路径与 --dry-run 预览，以及逐质粒落盘与中断保留。
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from abif_utils import make_ab1  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "batch_sequencing_report", _REPO / "scripts" / "batch_sequencing_report.py")
batch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(batch)


def _file(tmp_path, name, ext, stem):
    p = tmp_path / name
    p.write_bytes(b"x")
    return {"path": p, "rel": Path(name), "ext": ext, "stem": stem}


def _row(name, primers):
    return {"row": 2, "name": name, "primers": primers, "existing_result": None}


# ---------------------------------------------------------------- Excel 解析


def test_load_excel_parses_headers_rows(tmp_path):
    xlsx = tmp_path / "测序.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    ws.append(["MX", "A.ab1;B.ab1", None])
    ws.append(["pUC19", "T7.ab1", None])
    wb.save(xlsx)
    wb2, header_row, cols, rows = batch.load_excel(xlsx)
    assert header_row == 1
    assert cols["plasmid"] == 1 and cols["primer"] == 2 and cols["result"] == 3
    assert [r["name"] for r in rows] == ["MX", "pUC19"]
    assert rows[0]["primers"] == ["A.ab1", "B.ab1"]


# ---------------------------------------------------------------- 匹配


def test_match_by_primer_column_and_reference_name(tmp_path):
    files = [
        _file(tmp_path, "SZ1-T7.ab1", ".ab1", "sz1-t7"),
        _file(tmp_path, "SZ1-seqF.ab1", ".ab1", "sz1-seqf"),
        _file(tmp_path, "MX.dna", ".dna", "mx"),
        _file(tmp_path, "未知.ab1", ".ab1", "未知"),
        _file(tmp_path, "孤儿.dna", ".dna", "孤儿"),
    ]
    rows = [_row("MX", ["SZ1-T7", "SZ1-seqF"])]
    per, unmatched = batch.match_files(rows, files)
    assert per["MX"]["reference"]["file"]["path"].name == "MX.dna"
    assert [r["file"]["path"].name for r in per["MX"]["reads"]] == ["SZ1-T7.ab1", "SZ1-seqF.ab1"]
    assert {u["file"]["path"].name for u in unmatched} == {"未知.ab1", "孤儿.dna"}


def test_match_tolerates_case_and_ab1_suffix_in_excel(tmp_path):
    files = [_file(tmp_path, "sample-T7-TERM.ab1", ".ab1", "sample-t7-term")]
    rows = [_row("pET", ["Sample-t7-term.AB1"])]
    per, unmatched = batch.match_files(rows, files)
    assert not unmatched
    assert per["pET"]["reads"][0]["file"]["path"].name == "sample-T7-TERM.ab1"


def test_match_ab1_with_extra_prefix_unique_entry(tmp_path):
    """文件名带额外前缀（如 lane 号）时，唯一包含的引物条目仍可匹配。"""
    files = [_file(tmp_path, "L2-sample-T7.ab1", ".ab1", "l2-sample-t7")]
    rows = [_row("pET", ["sample-T7"])]
    per, unmatched = batch.match_files(rows, files)
    assert not unmatched
    assert per["pET"]["reads"][0]["how"].startswith("文件名包含引物条目")


# ---------------------------------------------------------------- 整理落盘


def test_write_archive_never_overwrites_different_data_copy(tmp_path):
    out = tmp_path / "out"
    entries = [("MX/正确/", None), ("MX/MX.dna", b"hello"),
               ("MX/正确/测序分析报告.md", "v1")]
    batch.write_archive(entries, out)
    assert (out / "MX" / "MX.dna").read_bytes() == b"hello"
    assert (out / "MX" / "正确").is_dir()
    # 重跑：同内容跳过；生成物更新；同名异内容的数据副本加序号另存
    batch.write_archive([("MX/MX.dna", b"hello"), ("MX/正确/测序分析报告.md", "v2")], out)
    assert not (out / "MX" / "MX (2).dna").exists()
    assert (out / "MX" / "正确" / "测序分析报告.md").read_text(encoding="utf-8") == "v2"
    batch.write_archive([("MX/MX.dna", b"other")], out)
    assert (out / "MX" / "MX.dna").read_bytes() == b"hello"
    assert (out / "MX" / "MX (2).dna").read_bytes() == b"other"


# ---------------------------------------------------------------- 结论


def _res(variants, cds, coverage=100.0, reads=1, errors=None):
    return {
        "reads": [{"filename": "x.ab1"}] * reads,
        "errors": errors or [],
        "variants": variants,
        "consensus": {"coverage_percent": coverage},
        "cds_reports": cds,
    }


def test_excel_conclusion不合格_点名CDS():
    """不合格主句只点名 CDS 与判读结果：verdict 里的移码/替换细节不进表
    （表格单元格以简洁为先，逐条证据在网页报告里）"""
    cds = [{"name": "MX", "coverage_status": "full", "protein_identical": False,
            "frameshift_count": 1,
            "verdict": "CDS 已完整覆盖。MX 蛋白与设计不一致：5076 处的插入使阅读框移码，"
                       "翻译提前终止（产物 25 aa，设计为 600 aa）；另有 1 处确证的碱基替换"
                       "（5269 C>T）。另有 5 处低置信差异（最高 Q 25，疑似测序噪声）未计入判定"}]
    res = _res([{"confidence": "medium"}, {"confidence": "high"}, {"confidence": "low"}], cds)
    out = batch.excel_conclusion("MX", res, 3, True)
    assert out.startswith("不合格：MX 蛋白与设计不一致（移码）")
    assert "5269 C>T" not in out and "详见报告" not in out
    assert "1 处低置信差异疑似伪影" in out


def test_excel_conclusion_编码区外差异():
    cds = [{"name": "MX", "coverage_status": "full", "protein_identical": True, "verdict": "x"}]
    v = {"confidence": "medium", "features": [{"name": "lac operator", "type": "other"}]}
    res = _res([v], cds, coverage=42.0)
    out = batch.excel_conclusion("MX", res, 1, True)
    assert out.startswith("合格：编码区蛋白一致")
    assert "lac operator" in out and "测序覆盖 42%" in out


def test_excel_conclusion_完全一致与缺参考():
    res = _res([], [], reads=2)
    assert batch.excel_conclusion("MX", res, 2, True) == "合格：与设计一致"
    assert "缺少同名参考图谱" in batch.excel_conclusion("MX", None, 2, False)
    failed = _res([], [], reads=0, errors=[{"filename": "a.ab1", "error": "修剪后过短"}])
    assert batch.excel_conclusion("MX", failed, 1, True).startswith("分析失败：修剪后过短")


def test_excel_conclusion_互检矛盾合并为疑似伪影():
    """简洁口径：互检矛盾与一般低置信不再分开解释，合并为
    "N 处低置信差异疑似伪影"；全净克隆输出恰好一句主句"""
    conflict = {"confidence": "low", "cross_read_conflict": {"reads": ["b.ab1"]}}
    res = _res([conflict, {"confidence": "low"}], [], coverage=42.0)
    out = batch.excel_conclusion("MX", res, 2, True)
    assert out == "合格：与设计一致；2 处低置信差异疑似伪影；测序覆盖 42%"
    assert "互检矛盾" not in out and "详见报告" not in out


def test_excel_conclusion_部分比对不给合格():
    """review 04 H1：有 read 大段未比对上参考（partial_alignment）时，未比对
    部分没有验证，与网页 △ 同口径——结论不得以 合格 开头，归入 无法判定/"""
    from app.sequencing_report import _conclusion_bucket
    res = _res([], [], coverage=1.0)
    res["reads"] = [{"filename": "x.ab1", "partial_alignment": {
        "aligned_read_len": 11, "trimmed_length": 700, "circular": True}}]
    out = batch.excel_conclusion("MX", res, 1, True)
    assert out.startswith("无法判定：部分比对（x.ab1 仅 11/700bp 比对上参考）")
    assert _conclusion_bucket(out) == "无法判定"
    # 有确证差异也不升级为 合格；确证 CDS 不一致仍是 不合格
    res["variants"] = [{"confidence": "high"}]
    out = batch.excel_conclusion("MX", res, 1, True)
    assert out.startswith("无法判定：") and "1 处确证差异" in out
    res["cds_reports"] = [{"name": "MX", "coverage_status": "partial",
                           "protein_identical": False}]
    assert batch.excel_conclusion("MX", res, 1, True).startswith("不合格：MX")


def test_excel_conclusion_实测过少不给合格():
    """无完整覆盖的 CDS 且实测不足 MIN_VERDICT_COVERED_BP → 无法判定；
    部分测序本身是常规策略：CDS 完整覆盖或实测足够时照常 合格"""
    from core.sanger.batch import MIN_VERDICT_COVERED_BP
    res = _res([], [], coverage=1.0)
    res["consensus"]["covered_ranges"] = [(5, 24)]
    out = batch.excel_conclusion("MX", res, 1, True)
    assert out.startswith("无法判定：测序覆盖不足（仅 20bp 实测）")
    res["consensus"]["covered_ranges"] = [(1, MIN_VERDICT_COVERED_BP)]
    assert batch.excel_conclusion("MX", res, 1, True).startswith("合格：与设计一致")
    res["consensus"]["covered_ranges"] = [(5, 24)]
    res["cds_reports"] = [{"name": "MX", "coverage_status": "full",
                           "protein_identical": True}]
    assert batch.excel_conclusion("MX", res, 1, True).startswith("合格：")


def _h1_case(read_fn):
    """review 04 H1 复现：2000 bp 环状参考（CDS 1..300），单条 read 跑主流程"""
    import logging
    import random

    from core.sanger.pipeline import analyze
    rng = random.Random(5)

    def rs(n):
        return "".join(rng.choice("ACGT") for _ in range(n))

    out = "ATG"
    while len(out) < 297:
        c = rs(3)
        if c not in ("TAA", "TAG", "TGA"):
            out += c
    ref = out + "TAA" + rs(1700)
    feats = [{"name": "CDS1", "type": "CDS", "start": 1, "end": 300,
              "strand": "+", "description": ""}]
    read = read_fn(ref, rs)
    logging.disable(logging.WARNING)
    try:
        return analyze([("r0.ab1", make_ab1(read, [40] * len(read)))],
                       ref, feats, circular=True)
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.parametrize("label,read_fn", [
    ("错质粒：与参考无关的随机 read", lambda ref, rs: rs(700)),
    ("前半来自参考、后半外源", lambda ref, rs: ref[300:700] + rs(400)),
])
def test_excel_conclusion_错质粒不入正确(label, read_fn):
    """review 04 H1 端到端：错质粒/污染 read 此前得到 "合格：与设计一致；
    测序覆盖 1%/20%" 并被归入 正确/"""
    from app.sequencing_report import _conclusion_bucket
    res = _h1_case(read_fn)
    out = batch.excel_conclusion("P1", res, 1, True)
    assert not out.startswith("合格"), (label, out)
    assert out.startswith("无法判定：")
    assert _conclusion_bucket(out) == "无法判定"


def test_excel_conclusion_完整比对仍合格():
    """对照：同一参考上完整比对的 read（覆盖 CDS）结论不变，仍归入 正确/"""
    from app.sequencing_report import _conclusion_bucket
    res = _h1_case(lambda ref, rs: ref[:700])
    out = batch.excel_conclusion("P1", res, 1, True)
    assert out.startswith("合格：与设计一致"), out
    assert _conclusion_bucket(out) == "正确"


# ---------------------------------------------------------------- 端到端


REF = ("ATGAAACGT" * 12 + "TAA")  # 123 bp，含起始/终止密码子


def _write_inputs(data_dir: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    ws.append(["TST", "T1", None])
    wb.save(data_dir / "测序.xlsx")
    (data_dir / "TST.fasta").write_text(f">TST\n{REF}\n", encoding="utf-8")
    (data_dir / "T1.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))


def test_end_to_end(tmp_path, monkeypatch):
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)

    monkeypatch.setattr(sys, "argv", [
        "batch_sequencing_report.py", "--data-dir", str(data_dir)])
    batch.main()

    out = data_dir / "测序分析"
    folder = out / "TST" / "正确"
    # 原件未动 + 副本齐全（图谱在质粒文件夹根，read 与报告按结论分置）
    assert (data_dir / "T1.ab1").exists()
    assert (out / "TST" / "TST.fasta").exists()
    assert (out / "TST" / "错误").is_dir()
    for name in ("T1.ab1", "测序分析报告.md", "分析结果.json"):
        assert (folder / name).exists(), name
    assert (out / "整理清单.csv").exists() and (out / "批次总览.txt").exists()
    manifest = (out / "整理清单.csv").read_text(encoding="utf-8-sig")
    assert "原始路径" in manifest and str(data_dir / "T1.ab1") in manifest
    assert (out / "测序_原始备份.xlsx").exists()
    # Excel 回填
    ws = openpyxl.load_workbook(data_dir / "测序.xlsx").worksheets[0]
    result = ws.cell(row=2, column=3).value
    assert result == "合格：与设计一致"
    # 报告内容：read 概况 + 一致结论
    report = (folder / "测序分析报告.md").read_text(encoding="utf-8")
    assert "合格：与设计一致" in report and "T1.ab1" in report
    payload = json.loads((folder / "分析结果.json").read_text(encoding="utf-8"))
    assert payload["variants"] == [] and "traces" not in payload


def test_end_to_end_missing_reference(tmp_path, monkeypatch):
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    ws.append(["TST", "T1", None])
    wb.save(data_dir / "测序.xlsx")
    (data_dir / "T1.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))

    monkeypatch.setattr(sys, "argv", [
        "batch_sequencing_report.py", "--data-dir", str(data_dir)])
    batch.main()

    ws = openpyxl.load_workbook(data_dir / "测序.xlsx").worksheets[0]
    assert "缺少同名参考图谱" in ws.cell(row=2, column=3).value
    out = data_dir / "测序分析"
    assert (out / "TST" / "无法判定" / "T1.ab1").exists()


# ---------------------------------------------------------------- 与网页整理包同产物


# 两端独立构造的同一批输入（不共用扫描结果或分析记录）：
#   MX   GenBank 图谱（整序列一个 CDS）+ 第 31 位 A>G 错义 read → 不合格 → 错误/
#   MIX  同一 GenBank 图谱 + 双峰混合 read                       → 无法判定/
#   PX   FASTA 图谱 + 两条一致 read                              → 合格 → 正确/
#   NOREF  只有 read，缺图谱                                     → 无法判定/
#   孤儿.ab1  不在任何引物列                                      → 未匹配文件/
_CMP_ROWS = [("MX", "M1"), ("MIX", "X1"), ("PX", "P1;P2"), ("NOREF", "Q1")]


def _cmp_inputs():
    from tests.test_sequencing_batch import REF_MX, REF_MX2, _gb_mx, _mixed_ab1_mx
    mut = REF_MX[:30] + "G" + REF_MX[31:]
    return {
        "MX.gb": _gb_mx(),
        "M1.ab1": make_ab1(mut, [40] * len(mut)),
        "MIX.gb": _gb_mx(),
        "X1.ab1": _mixed_ab1_mx("X1.ab1")[1],
        "PX.fasta": f">PX\n{REF_MX2}\n".encode(),
        "P1.ab1": make_ab1(REF_MX2, [40] * len(REF_MX2)),
        "P2.ab1": make_ab1(REF_MX2[6:], [40] * (len(REF_MX2) - 6)),
        "Q1.ab1": make_ab1(REF_MX, [40] * len(REF_MX)),
        "孤儿.ab1": make_ab1(REF_MX2, [40] * len(REF_MX2)),
    }


def _cmp_xlsx() -> bytes:
    import io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    for name, primers in _CMP_ROWS:
        ws.append([name, primers, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def web_client(monkeypatch):
    """网页端点：站点功能门控打桩为全开放（同 test_sequencing_batch）"""
    from fastapi.testclient import TestClient

    from app import site_settings
    from app.features import ALL_FEATURES
    from app.main import app
    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": list(ALL_FEATURES), "user_features": list(ALL_FEATURES)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))
    return TestClient(app)


def _web_package(client, inputs: dict, xlsx: bytes) -> dict:
    """POST /analyze-batch → GET 整理包，返回 {ZIP 内相对路径: 字节}（目录以 / 结尾）"""
    import io
    import zipfile

    from app.sequencing_report import ZIP_ROOT
    files = [("files", (n, b, "application/octet-stream")) for n, b in inputs.items()]
    files.append(("excel", ("测序.xlsx", xlsx, "application/octet-stream")))
    resp = client.post("/api/sequencing/analyze-batch", files=files, data={"min_q": "20"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["report_ready"] is True
    got = client.get(f"/api/sequencing/batches/{data['batch_id']}/report",
                     headers={"X-Access-Token": data["access_token"]})
    assert got.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(got.content))
    prefix = f"{ZIP_ROOT}/"
    assert all(n.startswith(prefix) for n in zf.namelist())
    return {n[len(prefix):]: zf.read(n) for n in zf.namelist()}


def _csv_rows(blob: bytes) -> list:
    import csv
    import io
    return list(csv.DictReader(io.StringIO(blob.decode("utf-8-sig"))))


def _cells(blob: bytes) -> list:
    import io
    ws = openpyxl.load_workbook(io.BytesIO(blob)).worksheets[0]
    return [[c.value for c in r] for r in ws.iter_rows()]


def _strip_volatile(md: str) -> str:
    import re
    return re.sub(r"^- 生成时间：.*$", "- 生成时间：<T>", md, flags=re.M)


_EXPECTED_CONCLUSIONS = {
    "MX": "不合格：MX 蛋白与设计不一致",
    "PX": "合格：与设计一致",
    "NOREF": "已整理 1 个测序文件，但缺少同名参考图谱（.dna/.gb/.fasta），无法自动比对",
}

_EXPECTED_FILES = {
    "MX/MX.gb", "MX/错误/M1.ab1", "MX/错误/测序分析报告.md", "MX/错误/分析结果.json",
    "MIX/MIX.gb", "MIX/无法判定/X1.ab1", "MIX/无法判定/测序分析报告.md",
    "MIX/无法判定/分析结果.json",
    "PX/PX.fasta", "PX/正确/P1.ab1", "PX/正确/P2.ab1", "PX/正确/测序分析报告.md",
    "PX/正确/分析结果.json",
    "NOREF/无法判定/Q1.ab1", "NOREF/无法判定/测序分析报告.md",
    "未匹配文件/孤儿.ab1", "整理清单.csv", "批次总览.txt",
}

# 两端允许的差异（review 05 I1/I2），其余逐字节/逐字段一致：
#   - 网页 ZIP 套一层 测序整理/；信息表在 ZIP 内给回填版 + 原始备份_<名>，离线
#     原地回填、备份为 <stem>_原始备份.xlsx 放输出目录；离线多 .整理记录.json
#   - 整理清单离线多一列「原始路径」；网页的归档路径带 测序整理/ 前缀
#   - 报告抬头「分析引擎」一行的运行位置措辞（网页批量分析 / 本地批处理）
#   - 分析结果.json：analysis_id、created_at 各端自生成；网页多 owner_id、
#     access_token_hash（服务器分析记录字段）
_WEB_ONLY_JSON_KEYS = {"owner_id", "access_token_hash"}


def test_offline_cli_matches_web_endpoint_package(tmp_path, monkeypatch, web_client):
    """review 05 M2：离线命令行落盘结果与网页端点整理包逐项对比，并对关键
    内容给出显式期望值（此前两端调同一函数自比，JSON/正文/Excel 都没比）"""
    import hashlib
    inputs = _cmp_inputs()
    xlsx = _cmp_xlsx()

    # 网页：真实端点（上传 → 分析 → 下载整理包）
    web = _web_package(web_client, inputs, xlsx)

    # 离线：同一批文件写到文件夹，跑命令行
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    for name, blob in inputs.items():
        (data_dir / name).write_bytes(blob)
    (data_dir / "测序.xlsx").write_bytes(xlsx)
    _run(monkeypatch, data_dir)
    out = data_dir / "测序分析"
    off = {p.relative_to(out).as_posix(): p.read_bytes()
           for p in out.rglob("*") if p.is_file()}

    # 1) 文件集合：两端相同且等于期望；信息表与整理记录按允许差异
    assert set(off) - _EXPECTED_FILES == {"测序_原始备份.xlsx", batch.RECORD_NAME}
    web_files = {n for n in web if not n.endswith("/")}
    assert web_files - _EXPECTED_FILES == {"测序.xlsx", "原始备份_测序.xlsx"}
    assert _EXPECTED_FILES <= set(off) and _EXPECTED_FILES <= web_files
    for d in ("MX/正确/", "MX/错误/", "PX/正确/", "PX/错误/", "NOREF/正确/"):
        assert d in web and (out / d).is_dir(), d

    # 2) 数据副本与原件逐字节一致
    for arc in _EXPECTED_FILES:
        name = arc.rsplit("/", 1)[-1]
        if name in inputs:
            assert off[arc] == inputs[name] == web[arc], arc

    # 3) 报告全文：只允许生成时间与「分析引擎」一行的运行位置措辞不同
    for arc in sorted(a for a in _EXPECTED_FILES if a.endswith(".md")):
        o = _strip_volatile(off[arc].decode("utf-8"))
        w = _strip_volatile(web[arc].decode("utf-8"))
        if "- 分析引擎：" in w:
            assert "本地批处理" in o and "网页批量分析" in w, arc
            w = w.replace("网页批量分析", "本地批处理").replace("服务器未安装", "本机未安装")
        assert o == w, arc
    mx_md = off["MX/错误/测序分析报告.md"].decode("utf-8")
    assert "> **不合格：MX 蛋白与设计不一致**" in mx_md
    assert "- 参考图谱：MX.gb（111 bp，1 个特征）" in mx_md
    assert ("各编码区（CDS）结论：\n\n- ✅ **MX**（100.0% 覆盖）：CDS 已完整覆盖。"
            "MX 蛋白与设计不一致：1 处氨基酸替换（K11E）\n") in mx_md
    assert "| 31 | substitution | A>G | 高 | 40 | 1 | 峰强比 100% | MX | MX:K11E |" in mx_md
    assert "| M1.ab1 | 测序 read | Excel 引物列精确匹配 |" in mx_md
    mix_md = off["MIX/无法判定/测序分析报告.md"].decode("utf-8")
    assert "## 双峰（疑似混合）检测" in mix_md and "**疑似混合样品**" in mix_md
    noref_md = off["NOREF/无法判定/测序分析报告.md"].decode("utf-8")
    assert "- 未运行序列分析" in noref_md and "- 参考图谱：缺失" in noref_md

    # 4) 分析结果.json：键与值一致（允许差异见上），关键值显式断言
    for arc in sorted(a for a in _EXPECTED_FILES if a.endswith(".json")):
        o = json.loads(off[arc])
        w = json.loads(web[arc])
        assert set(w) - set(o) == _WEB_ONLY_JSON_KEYS, arc
        assert set(o) <= set(w), arc
        assert o["analysis_id"].startswith("local_") and w["analysis_id"] != o["analysis_id"]
        for k in set(o) - {"analysis_id", "created_at"}:
            assert o[k] == w[k], (arc, k)
        assert "traces" not in o and "reference" not in o and "_trace_data" not in w
    mx = json.loads(off["MX/错误/分析结果.json"])
    assert mx["sample_name"] == "MX"
    assert [(v["ref_pos"], v["ref_base"], v["alt_base"], v["confidence"])
            for v in mx["variants"]] == [(31, "A", "G", "high")]
    assert [(c["name"], c["coverage_status"], c["protein_identical"])
            for c in mx["cds_reports"]] == [("MX", "full", False)]
    px = json.loads(off["PX/正确/分析结果.json"])
    assert px["variants"] == [] and [r["filename"] for r in px["reads"]] == ["P1.ab1", "P2.ab1"]
    assert json.loads(off["MIX/无法判定/分析结果.json"])["mixed_detected"] == {
        "X1.ab1": [31, 46, 61, 76, 91]}

    # 5) 整理清单：除离线独有的「原始路径」列外逐行一致，并与期望值吻合
    o_rows, w_rows = _csv_rows(off["整理清单.csv"]), _csv_rows(web["整理清单.csv"])
    assert [r.pop("原始路径") for r in o_rows] == [
        str(data_dir / r["文件"]) for r in o_rows]
    assert all(r["归档路径"].startswith("测序整理/") for r in w_rows)
    assert o_rows == [{**r, "归档路径": r["归档路径"][len("测序整理/"):]} for r in w_rows]
    md5 = {n: hashlib.md5(b).hexdigest() for n, b in inputs.items()}
    assert [(r["质粒"], r["文件"], r["类型"], r["归档路径"], r["MD5"]) for r in o_rows] == [
        ("MX", "MX.gb", "参考图谱", "MX/MX.gb", md5["MX.gb"]),
        ("MX", "M1.ab1", "测序 read", "MX/错误/M1.ab1", md5["M1.ab1"]),
        ("MIX", "MIX.gb", "参考图谱", "MIX/MIX.gb", md5["MIX.gb"]),
        ("MIX", "X1.ab1", "测序 read", "MIX/无法判定/X1.ab1", md5["X1.ab1"]),
        ("PX", "PX.fasta", "参考图谱", "PX/PX.fasta", md5["PX.fasta"]),
        ("PX", "P1.ab1", "测序 read", "PX/正确/P1.ab1", md5["P1.ab1"]),
        ("PX", "P2.ab1", "测序 read", "PX/正确/P2.ab1", md5["P2.ab1"]),
        ("NOREF", "Q1.ab1", "测序 read", "NOREF/无法判定/Q1.ab1", md5["Q1.ab1"]),
        ("", "孤儿.ab1", "未匹配", "未匹配文件/孤儿.ab1", md5["孤儿.ab1"]),
    ]
    by_file = {r["文件"]: r["结论"] for r in o_rows}
    assert by_file["M1.ab1"] == _EXPECTED_CONCLUSIONS["MX"]
    assert by_file["P1.ab1"] == by_file["P2.ab1"] == _EXPECTED_CONCLUSIONS["PX"]
    assert by_file["Q1.ab1"] == _EXPECTED_CONCLUSIONS["NOREF"]
    assert by_file["X1.ab1"].startswith("疑似混合：X1.ab1 检出")

    # 6) 批次总览：除生成时间外逐字一致
    o_txt = off["批次总览.txt"].decode("utf-8").splitlines()
    w_txt = web["批次总览.txt"].decode("utf-8").splitlines()
    assert o_txt[1].startswith("生成时间：") and w_txt[1].startswith("生成时间：")
    assert o_txt[:1] + o_txt[2:] == w_txt[:1] + w_txt[2:]
    assert f"  [analyzed] PX — reads=2，覆盖 100.0%，确证差异 0 — {_EXPECTED_CONCLUSIONS['PX']}" in o_txt
    assert "  孤儿.ab1 — 未出现在任何质粒的引物列中" in o_txt

    # 7) 信息表：离线原地回填结果 == 网页 ZIP 回填版；备份均为原表
    off_cells = _cells((data_dir / "测序.xlsx").read_bytes())
    assert off_cells == _cells(web["测序.xlsx"])
    assert {r[0]: r[2] for r in off_cells[1:]} == {
        "MX": _EXPECTED_CONCLUSIONS["MX"], "MIX": by_file["X1.ab1"],
        "PX": _EXPECTED_CONCLUSIONS["PX"], "NOREF": _EXPECTED_CONCLUSIONS["NOREF"]}
    assert _cells(off["测序_原始备份.xlsx"]) == _cells(web["原始备份_测序.xlsx"]) \
        == _cells(xlsx)


# ---------------------------------------------------------------- 重跑同一输出目录


def _run(monkeypatch, data_dir, *extra):
    monkeypatch.setattr(sys, "argv", [
        "batch_sequencing_report.py", "--data-dir", str(data_dir), *extra])
    batch.main()


def _manifest_paths(out: Path) -> set:
    import csv
    import io
    text = (out / "整理清单.csv").read_text(encoding="utf-8-sig")
    return {r["归档路径"] for r in csv.DictReader(io.StringIO(text))}


def test_rerun_removes_stale_copy_after_verdict_change(tmp_path, monkeypatch):
    """判定从 正确 变为 无法判定：上次留在 正确/ 的 read、报告与 JSON 被清理，
    用户放进去的文件与改过的生成物保留，正确/ 错误/ 不删，原件不动"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    out = data_dir / "测序分析"
    _run(monkeypatch, data_dir, "--no-write-back")
    ok = out / "TST" / "正确"
    assert (ok / "T1.ab1").exists() and (out / batch.RECORD_NAME).exists()

    (ok / "笔记.txt").write_text("用户自己的备注", encoding="utf-8")
    (out / "TST" / "随手记.md").write_text("记录之外", encoding="utf-8")
    (ok / "分析结果.json").write_text("{\"edited\": true}", encoding="utf-8")

    (data_dir / "TST.fasta").unlink()       # 缺图谱 → 无法判定
    _run(monkeypatch, data_dir, "--no-write-back")

    assert (out / "TST" / "无法判定" / "T1.ab1").exists()
    assert not (ok / "T1.ab1").exists()
    assert not (ok / "测序分析报告.md").exists()
    assert not (out / "TST" / "TST.fasta").exists()     # 上次的图谱副本也已过期
    assert (ok / "笔记.txt").read_text(encoding="utf-8") == "用户自己的备注"
    assert (out / "TST" / "随手记.md").exists()
    assert (ok / "分析结果.json").read_text(encoding="utf-8") == "{\"edited\": true}"
    assert (out / "TST" / "错误").is_dir()
    assert (data_dir / "T1.ab1").exists()
    assert _manifest_paths(out) == {"TST/无法判定/T1.ab1"}


def test_rerun_prunes_empty_dirs_but_keeps_correct_and_wrong(tmp_path, monkeypatch):
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    out = data_dir / "测序分析"
    (data_dir / "TST.fasta").unlink()
    _run(monkeypatch, data_dir, "--no-write-back")
    assert (out / "TST" / "无法判定" / "T1.ab1").exists()

    (data_dir / "TST.fasta").write_text(f">TST\n{REF}\n", encoding="utf-8")
    _run(monkeypatch, data_dir, "--no-write-back")
    assert (out / "TST" / "正确" / "T1.ab1").exists()
    assert not (out / "TST" / "无法判定").exists()      # 清空后删除
    assert (out / "TST" / "错误").is_dir()              # 空也保留


def test_rerun_legacy_manifest_only_cleans_matching_data_copies(tmp_path, monkeypatch):
    """旧版本跑出的目录没有整理记录：退回 整理清单.csv 识别数据副本"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    out = data_dir / "测序分析"
    _run(monkeypatch, data_dir, "--no-write-back")
    (out / batch.RECORD_NAME).unlink()
    (data_dir / "TST.fasta").unlink()
    _run(monkeypatch, data_dir, "--no-write-back")
    assert not (out / "TST" / "正确" / "T1.ab1").exists()
    assert (out / "TST" / "无法判定" / "T1.ab1").exists()


def test_rerun_same_name_conflict_manifest_records_written_path(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    out = data_dir / "测序分析"
    _run(monkeypatch, data_dir, "--no-write-back")
    ok = out / "TST" / "正确"
    (ok / "T1.ab1").write_bytes(b"user replaced this read")   # 用户改动 → 不可覆盖

    _run(monkeypatch, data_dir, "--no-write-back")
    assert "副本另存为 T1 (2).ab1" in capsys.readouterr().out
    assert (ok / "T1.ab1").read_bytes() == b"user replaced this read"
    assert (ok / "T1 (2).ab1").read_bytes() == (data_dir / "T1.ab1").read_bytes()
    assert _manifest_paths(out) == {"TST/TST.fasta", "TST/正确/T1 (2).ab1"}

    _run(monkeypatch, data_dir, "--no-write-back")     # 再跑：复用已有副本，不再加号
    assert not (ok / "T1 (3).ab1").exists()
    assert (ok / "T1 (2).ab1").exists()
    assert _manifest_paths(out) == {"TST/TST.fasta", "TST/正确/T1 (2).ab1"}


def test_rerun_dry_run_reports_without_removing(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    out = data_dir / "测序分析"
    _run(monkeypatch, data_dir, "--no-write-back")
    before = {p.relative_to(out).as_posix(): p.read_bytes()
              for p in out.rglob("*") if p.is_file()}
    xlsx_before = (data_dir / "测序.xlsx").read_bytes()
    capsys.readouterr()

    (data_dir / "TST.fasta").unlink()
    _run(monkeypatch, data_dir, "--dry-run")
    text = capsys.readouterr().out
    assert "[预览] 将清理上次生成的旧副本：TST/正确/T1.ab1" in text
    assert "TST/正确/测序分析报告.md" in text
    after = {p.relative_to(out).as_posix(): p.read_bytes()
             for p in out.rglob("*") if p.is_file()}
    assert after == before
    assert not (out / "TST" / "无法判定").exists()
    assert (data_dir / "测序.xlsx").read_bytes() == xlsx_before


# ---------------------------------------------------------------- 逐质粒落盘（review 05 M4）


def _write_two_plasmids(data_dir: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    ws.append(["TST", "T1", None])
    ws.append(["TS2", "T2", None])
    wb.save(data_dir / "测序.xlsx")
    for name, read in (("TST", "T1"), ("TS2", "T2")):
        (data_dir / f"{name}.fasta").write_text(f">{name}\n{REF}\n", encoding="utf-8")
        (data_dir / f"{read}.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))


def _crash_on_second_analyze(monkeypatch):
    """第 2 次跑管线时模拟 Ctrl-C（BaseException，不会被单组容错吞掉）"""
    import app.sequencing_batch as sb
    real = sb.analyze
    calls = []

    def fake(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(*a, **kw)
    monkeypatch.setattr(sb, "analyze", fake)
    return calls


def test_interrupted_run_keeps_finished_plasmids(tmp_path, monkeypatch):
    """中途中断：已分析完的质粒文件夹（副本、报告、JSON）与整理清单行已落盘，
    此前整批跑完才写，中断即一个文件都没有"""
    import csv
    import io
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_two_plasmids(data_dir)
    _crash_on_second_analyze(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, data_dir, "--no-write-back")

    out = data_dir / "测序分析"
    ok = out / "TST" / "正确"
    assert (out / "TST" / "TST.fasta").read_bytes() == (data_dir / "TST.fasta").read_bytes()
    assert (ok / "T1.ab1").read_bytes() == (data_dir / "T1.ab1").read_bytes()
    assert "合格：与设计一致" in (ok / "测序分析报告.md").read_text(encoding="utf-8")
    assert json.loads((ok / "分析结果.json").read_text(encoding="utf-8"))["sample_name"] == "TST"
    assert not (out / "TS2").exists()
    rows = list(csv.DictReader(io.StringIO(
        (out / "整理清单.csv").read_text(encoding="utf-8-sig"))))
    assert [(r["质粒"], r["归档路径"]) for r in rows] == [
        ("TST", "TST/TST.fasta"), ("TST", "TST/正确/T1.ab1")]
    assert (out / batch.JOURNAL_NAME).exists()


def test_rerun_after_interrupt_cleans_from_journal(tmp_path, monkeypatch):
    """中断留下的进行中记录在下次运行时参与清理：判定变化后，中断那次写到
    正确/ 的副本被识别为本工具所写并清理；正常结束后进行中记录并入整理记录"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_two_plasmids(data_dir)
    _crash_on_second_analyze(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, data_dir, "--no-write-back")
    monkeypatch.undo()

    out = data_dir / "测序分析"
    (data_dir / "TST.fasta").unlink()       # 缺图谱 → 无法判定
    _run(monkeypatch, data_dir, "--no-write-back")
    assert not (out / "TST" / "正确" / "T1.ab1").exists()
    assert not (out / "TST" / "正确" / "测序分析报告.md").exists()
    assert not (out / "TST" / "TST.fasta").exists()
    assert (out / "TST" / "无法判定" / "T1.ab1").exists()
    assert (out / "TS2" / "正确" / "T2.ab1").exists()
    assert not (out / batch.JOURNAL_NAME).exists()
    rec = json.loads((out / batch.RECORD_NAME).read_text(encoding="utf-8"))["files"]
    assert "TS2/正确/T2.ab1" in rec and "TST/正确/T1.ab1" not in rec
    assert _manifest_paths(out) == {"TST/无法判定/T1.ab1", "TS2/TS2.fasta",
                                    "TS2/正确/T2.ab1"}


def test_bytes_released_after_each_plasmid(tmp_path, monkeypatch):
    """分析第 2 个质粒时，第 1 个质粒的文件字节已随其文件夹落盘释放，
    且第 2 个质粒的文件此前尚未读入（内存不随批次线性增长）"""
    import app.sequencing_batch as sb
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_two_plasmids(data_dir)
    _wb, _h, _cols, rows = batch.load_excel(data_dir / "测序.xlsx")
    out = tmp_path / "out"
    files = batch.scan_files(data_dir, out)
    by_name = {f["name"]: f for f in files}
    seen = []
    real = sb.analyze

    def spy(*a, **kw):
        seen.append({n: "bytes" in f for n, f in by_name.items()})
        if len(seen) == 2:
            assert (out / "TST" / "正确" / "T1.ab1").exists()
        return real(*a, **kw)
    monkeypatch.setattr(sb, "analyze", spy)
    batch.run_to_dir(files, rows, out, 20)
    assert seen == [
        {"TST.fasta": True, "T1.ab1": True, "TS2.fasta": False, "T2.ab1": False},
        {"TST.fasta": False, "T1.ab1": False, "TS2.fasta": True, "T2.ab1": True},
    ]
    assert not any("bytes" in f for f in files)


def test_streamed_entries_match_one_shot_build(tmp_path):
    """逐质粒构建的条目与整批一次构建（batch_archive_entries）完全一致：
    含表内别名并档（'17648' 与 '17648 MBYSTC' 隔行出现、结论不同）、同名 read
    加序号、未匹配文件；整理清单行序也相同"""
    from app.sequencing_report import batch_archive_entries
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    (data_dir / "17648 MBYSTC.fasta").write_text(f">a\n{REF}\n", encoding="utf-8")
    (data_dir / "A1.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))
    (data_dir / "A2.ab1").write_bytes(make_ab1(REF[:90], [40] * 90))
    (data_dir / "TST.fasta").write_text(f">TST\n{REF}\n", encoding="utf-8")
    (data_dir / "T1.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))
    (data_dir / "sub").mkdir()
    (data_dir / "sub" / "T1.ab1").write_bytes(make_ab1(REF[3:], [40] * (len(REF) - 3)))
    (data_dir / "孤儿.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    ws.append(["17648", "A2", None])
    ws.append(["TST", "T1", None])
    ws.append(["17648 MBYSTC", "A1", None])
    wb.save(data_dir / "测序.xlsx")
    _wb, _h, _cols, rows = batch.load_excel(data_dir / "测序.xlsx")
    files = batch.scan_files(data_dir, tmp_path / "out")

    records = {}

    class _Keep(batch.OfflineBatch):      # 留住分析记录，供整批再构建一次
        def _register(self, *a):
            aid, rec = super()._register(*a)
            records[aid] = rec
            return aid, rec

    run = _Keep(files, rows, 20)
    streamed = [e for chunk in run.chunks() for e in chunk.entries]
    batch.load_bytes(files)
    one_shot = batch_archive_entries(
        run.payload, run.groups, run.unmatched, None, None, None, records,
        origin="offline", source_paths=True, root="")

    def norm(entries):
        import re
        return sorted((a, re.sub(r"生成时间：.*", "", d) if isinstance(d, str) else d)
                      for a, d in entries)
    assert norm(streamed) == norm(one_shot)
    manifest = dict(streamed)["整理清单.csv"]
    assert [ln.split(",")[6] for ln in manifest.splitlines()[1:]] == [
        "17648 MBYSTC/无法判定/A2.ab1", "TST/TST.fasta", "TST/正确/T1.ab1",
        "TST/正确/T1 (2).ab1", "17648 MBYSTC/17648 MBYSTC.fasta",
        "17648 MBYSTC/正确/A1.ab1", "未匹配文件/孤儿.ab1"]


def _manifest_md5_matches_files(out: Path) -> list:
    """整理清单每一行：归档路径上实际文件的 MD5 与清单 MD5 列一致"""
    import csv
    import hashlib
    import io
    rows = list(csv.DictReader(io.StringIO(
        (out / "整理清单.csv").read_text(encoding="utf-8-sig"))))
    return [(r["归档路径"], r["MD5"] == hashlib.md5(
        (out / r["归档路径"]).read_bytes()).hexdigest()) for r in rows]


def test_rerun_renamed_copies_manifest_md5_points_at_written_file(tmp_path, monkeypatch):
    """review 05 M1：同名异内容加 " (n)" 另存后，清单的归档路径 + MD5 必须指向
    实际写出的那份副本（read 与参考图谱都覆盖），MD5 等于原件；再跑仍成立"""
    import hashlib
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    out = data_dir / "测序分析"
    _run(monkeypatch, data_dir, "--no-write-back")
    (out / "TST" / "正确" / "T1.ab1").write_bytes(b"user replaced this read")
    (out / "TST" / "TST.fasta").write_text(">edited\nACGT\n", encoding="utf-8")

    for _ in range(2):
        _run(monkeypatch, data_dir, "--no-write-back")
        assert _manifest_paths(out) == {"TST/TST (2).fasta", "TST/正确/T1 (2).ab1"}
        assert all(ok for _p, ok in _manifest_md5_matches_files(out)), \
            _manifest_md5_matches_files(out)
        rec = json.loads((out / batch.RECORD_NAME).read_text(encoding="utf-8"))["files"]
        for rel, src in (("TST/正确/T1 (2).ab1", "T1.ab1"), ("TST/TST (2).fasta", "TST.fasta")):
            assert rec[rel] == hashlib.md5((data_dir / src).read_bytes()).hexdigest()
        assert "TST/正确/T1.ab1" not in rec and "TST/TST.fasta" not in rec


def test_interrupted_rerun_partial_manifest_md5_points_at_written_file(tmp_path, monkeypatch):
    """逐质粒追加的清单行同样记录 " (n)" 另存后的实际路径：中断时留下的
    半份清单也能按归档路径 + MD5 追溯到正确的副本"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_two_plasmids(data_dir)
    out = data_dir / "测序分析"
    _run(monkeypatch, data_dir, "--no-write-back")
    (out / "TST" / "正确" / "T1.ab1").write_bytes(b"user replaced this read")
    _crash_on_second_analyze(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, data_dir, "--no-write-back")
    assert _manifest_paths(out) == {"TST/TST.fasta", "TST/正确/T1 (2).ab1"}
    assert all(ok for _p, ok in _manifest_md5_matches_files(out))


# ---------------------------------------------------------------- 换输出目录重跑


def _scanned_names(data_dir: Path, out: Path) -> list:
    return sorted(f["rel"].as_posix() for f in batch.scan_files(data_dir, out))


def test_rerun_other_out_dir_skips_previous_output_tree(tmp_path, monkeypatch, capsys):
    """review 05 M5：换 --out-dir 重跑时，上次默认输出目录 测序分析/ 里的
    read 与图谱副本不能被当成新输入（否则旧副本参与共识、图谱进未匹配）"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    _run(monkeypatch, data_dir, "--no-write-back")
    assert (data_dir / "测序分析" / "TST" / "正确" / "T1.ab1").exists()

    out2 = tmp_path / "out2"
    assert _scanned_names(data_dir, out2) == ["T1.ab1", "TST.fasta"]
    capsys.readouterr()
    _run(monkeypatch, data_dir, "--no-write-back", "--out-dir", str(out2))
    log = capsys.readouterr().out
    assert "扫描到 2 个文件（1 个 .ab1，1 个图谱）" in log
    assert "测序分析" in log and "跳过" in log
    assert "· TST: 1 read" in log
    assert not (out2 / "未匹配文件").exists()
    assert _manifest_paths(out2) == {"TST/TST.fasta", "TST/正确/T1.ab1"}


@pytest.mark.parametrize("marker", ["record", "journal", "manifest", "legacy_manifest"])
def test_scan_skips_output_tree_detected_by_marker(tmp_path, marker):
    """改过名或挪过位置的旧输出目录：凭整理记录 / 进行中记录 / 整理清单表头识别"""
    data_dir = tmp_path / "results"
    old = data_dir / "上周" / "结果备份"
    (old / "P").mkdir(parents=True)
    (data_dir / "P1.ab1").write_bytes(b"r")
    (old / "P" / "P1.ab1").write_bytes(b"old copy")
    (old / "P.fasta").write_text(">P\nACGT\n", encoding="utf-8")
    if marker == "record":
        (old / batch.RECORD_NAME).write_text('{"version": 1, "files": {}}', encoding="utf-8")
    elif marker == "journal":
        (old / batch.JOURNAL_NAME).write_text("", encoding="utf-8")
    elif marker == "manifest":
        (old / batch.MANIFEST_CSV).write_text(
            "\ufeff质粒,克隆,文件,类型,结论,匹配方式,归档路径,大小KB,MD5,原始路径\n",
            encoding="utf-8")
    else:
        (old / batch.MANIFEST_CSV).write_text(
            "plasmid,name,kind,how,src,dst,kb,md5\n", encoding="utf-8")
    assert _scanned_names(data_dir, tmp_path / "out") == ["P1.ab1"]
    assert batch.previous_output_dirs(data_dir) == [old]


def test_scan_keeps_user_folder_with_unrelated_manifest_name(tmp_path):
    """只是碰巧叫 整理清单.csv、表头不是本工具格式的用户文件夹照常扫描；
    数据文件夹本身带整理记录（如曾把输出写进数据目录）也不整体跳过"""
    data_dir = tmp_path / "results"
    sub = data_dir / "plate1"
    sub.mkdir(parents=True)
    (sub / "P1.ab1").write_bytes(b"r")
    (sub / batch.MANIFEST_CSV).write_text("样品,备注\nP1,ok\n", encoding="utf-8")
    (data_dir / batch.RECORD_NAME).write_text('{"version": 1, "files": {}}',
                                             encoding="utf-8")
    (data_dir / "P.fasta").write_text(">P\nACGT\n", encoding="utf-8")
    assert _scanned_names(data_dir, tmp_path / "out") == ["P.fasta", "plate1/P1.ab1"]
    assert batch.previous_output_dirs(data_dir) == []


def test_clone_mode_error_points_to_real_frontend_route(tmp_path, monkeypatch):
    """review 10 L8：克隆模式信息表的拒绝提示要指向前端真实存在的批量测序
    页面路由（src/frontend/src/router/index.ts），而不是不存在的旧路径"""
    import re
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "克隆号", "测序引物", "测序结果"])
    ws.append(["TST", "C1", "M13F", None])
    wb.save(data_dir / "测序.xlsx")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, data_dir)
    msg = str(exc.value)
    route = re.search(r"（(/[^）]+)）", msg).group(1)
    router = (_REPO / "src" / "frontend" / "src" / "router" / "index.ts").read_text(
        encoding="utf-8")
    m = re.search(r"path:\s*'([^']+)',\s*name:\s*'sequencing-batch'", router)
    assert m, "前端路由表里找不到 sequencing-batch"
    assert route == m.group(1) == "/sequencing/batch"


# ---------------------------------------------------------------- 参数与错误处理


@pytest.mark.parametrize("where", ["same", "parent", "file"])
def test_invalid_out_dir_rejected_before_writing(tmp_path, monkeypatch, where):
    """review 05 L1：--out-dir 等于数据文件夹（或其上级）时此前扫描到 0 个文件、
    把清单/总览写进数据目录并回填「未找到」；是普通文件时抛 traceback。
    现在一律在写任何东西之前中文报错退出，信息表不动"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    target = {"same": data_dir, "parent": tmp_path,
              "file": tmp_path / "afile"}[where]
    if where == "file":
        target.write_text("x", encoding="utf-8")
    before = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, data_dir, "--out-dir", str(target))
    assert isinstance(exc.value.code, str) and "输出目录" in exc.value.code
    assert {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()} == before
    assert not (tmp_path / batch.MANIFEST_CSV).exists()


@pytest.mark.parametrize("kind", ["junk", "not_workbook"])
def test_unreadable_excel_gives_message_not_traceback(tmp_path, monkeypatch, kind):
    """review 05 L1：损坏或并非工作簿的 .xlsx 此前直接抛 BadZipFile/KeyError"""
    import io
    import zipfile
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    if kind == "junk":
        blob = b"PK\x03\x04junk"
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.txt", "x")
        blob = buf.getvalue()
    (data_dir / "测序.xlsx").write_bytes(blob)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, data_dir)
    assert isinstance(exc.value.code, str)
    assert "无法读取信息表" in exc.value.code and "测序.xlsx" in exc.value.code
    assert not (data_dir / "测序分析").exists()


def test_write_back_locked_excel_keeps_original_and_explains(tmp_path, monkeypatch):
    """review 05 L1：回填直接 write_bytes 覆盖原表（非原子），表被 Excel 占用
    （Windows PermissionError）时只有 traceback。现在先写临时文件再替换；
    替换失败时原表原样、不留临时文件，提示关闭后重跑，归档与备份已写出"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_inputs(data_dir)
    xlsx = data_dir / "测序.xlsx"
    original = xlsx.read_bytes()

    real_wb, real_replace = Path.write_bytes, os.replace

    def locked_write(self, data):
        if self == xlsx:
            raise PermissionError(13, "Permission denied", str(self))
        return real_wb(self, data)

    def locked_replace(src, dst, *a, **kw):
        if Path(dst) == xlsx:
            raise PermissionError(13, "Permission denied", str(dst))
        return real_replace(src, dst, *a, **kw)
    monkeypatch.setattr(Path, "write_bytes", locked_write)
    monkeypatch.setattr(os, "replace", locked_replace)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, data_dir)
    assert isinstance(exc.value.code, str) and "回填" in exc.value.code
    assert "正被" in exc.value.code
    assert xlsx.read_bytes() == original
    assert sorted(p.name for p in data_dir.iterdir()) == [
        "T1.ab1", "TST.fasta", "测序.xlsx", "测序分析"]
    out = data_dir / "测序分析"
    assert (out / "TST" / "正确" / "T1.ab1").exists()
    assert (out / "测序_原始备份.xlsx").read_bytes() == original


# ---------------------------------------------------------------- 退出码


def _write_mixed_inputs(data_dir: Path, broken: bool):
    """TST 正常可判定、NOREF 缺图谱（无法判定，属结果）；broken 时再加一个
    参考序列过短、管线报「分析失败」的 BAD"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    ws.append(["TST", "T1", None])
    ws.append(["NOREF", "N1", None])
    if broken:
        ws.append(["BAD", "B1", None])
    wb.save(data_dir / "测序.xlsx")
    (data_dir / "TST.fasta").write_text(f">TST\n{REF}\n", encoding="utf-8")
    (data_dir / "T1.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))
    (data_dir / "N1.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))
    if broken:
        (data_dir / "BAD.fasta").write_text(">BAD\nATGAAACGT\n", encoding="utf-8")
        (data_dir / "B1.ab1").write_bytes(make_ab1(REF, [40] * len(REF)))


@pytest.mark.parametrize("extra", [(), ("--no-write-back",)])
def test_exit_code_zero_when_no_analysis_failed(tmp_path, monkeypatch, capsys, extra):
    """全部分析完成时返回 0；「无法判定」（缺图谱等数据原因）是结果不是失败"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_mixed_inputs(data_dir, broken=False)
    monkeypatch.setattr(sys, "argv", [
        "batch_sequencing_report.py", "--data-dir", str(data_dir), *extra])
    assert batch.main() == batch.EXIT_OK == 0
    out = data_dir / "测序分析"
    assert (out / "NOREF" / "无法判定" / "N1.ab1").exists()
    assert "分析失败" not in capsys.readouterr().out


@pytest.mark.parametrize("extra", [(), ("--no-write-back",)])
def test_exit_code_nonzero_when_analysis_failed(tmp_path, monkeypatch, capsys, extra):
    """有质粒分析失败时整批照常写出与回填，但返回 EXIT_ANALYSIS_FAILED，
    并以中文汇总行给出失败个数，调用方不再把部分失败当成全部成功"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_mixed_inputs(data_dir, broken=True)
    monkeypatch.setattr(sys, "argv", [
        "batch_sequencing_report.py", "--data-dir", str(data_dir), *extra])
    code = batch.main()
    assert code == batch.EXIT_ANALYSIS_FAILED == 3
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1].startswith("× ") and "1 个质粒分析失败" in lines[-1]
    assert "BAD" in lines[-1] and "退出码 3" in lines[-1]
    out = data_dir / "测序分析"
    assert (out / "TST" / "正确" / "T1.ab1").exists()
    assert (out / "BAD" / "无法判定" / "B1.ab1").exists()
    cells = {r[0].value: r[2].value for r in
             openpyxl.load_workbook(data_dir / "测序.xlsx").worksheets[0].iter_rows(min_row=2)}
    if extra:
        assert cells["BAD"] is None
    else:
        assert cells["BAD"].startswith("分析失败：")


def test_dry_run_exit_code_zero_even_with_failures(tmp_path, monkeypatch):
    """--dry-run 只是预览，不做判定，退出码恒为 0（致命错误除外）"""
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_mixed_inputs(data_dir, broken=True)
    _run(monkeypatch, data_dir)
    monkeypatch.setattr(sys, "argv", [
        "batch_sequencing_report.py", "--data-dir", str(data_dir), "--dry-run"])
    assert batch.main() == 0


@pytest.mark.parametrize("broken,expected", [(False, 0), (True, 3)])
def test_process_exit_code(tmp_path, broken, expected):
    """真实进程退出码：命令行入口把 main() 的返回值交给 sys.exit"""
    import subprocess
    data_dir = tmp_path / "results"
    data_dir.mkdir()
    _write_mixed_inputs(data_dir, broken=broken)
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "batch_sequencing_report.py"),
         "--data-dir", str(data_dir), "--no-write-back"],
        capture_output=True, text=True, encoding="utf-8", timeout=100)
    assert proc.returncode == expected, proc.stderr
    assert ("分析失败" in proc.stdout.strip().splitlines()[-1]) == broken


def test_fatal_and_usage_exit_codes_unchanged(tmp_path, monkeypatch):
    """致命错误仍为 SystemExit(中文消息) → 1，参数错误仍为 argparse 的 2；
    部分失败取 3，不与两者混淆"""
    monkeypatch.setattr(sys, "argv", [
        "batch_sequencing_report.py", "--data-dir", str(tmp_path / "nope")])
    with pytest.raises(SystemExit) as exc:
        batch.main()
    assert isinstance(exc.value.code, str) and "数据文件夹不存在" in exc.value.code
    monkeypatch.setattr(sys, "argv", ["batch_sequencing_report.py"])
    with pytest.raises(SystemExit) as exc:
        batch.main()
    assert exc.value.code == 2
    assert batch.EXIT_ANALYSIS_FAILED not in (1, 2)
