"""批量测序整理分析脚本测试（scripts/batch_sequencing_report.py）

覆盖：Excel 解析、文件名匹配（引物列/质粒名/未匹配）、整理复制（只复制不覆盖）、
Excel 结论各分支、以及"信息表 + 图谱 + .ab1 → 质粒文件夹 + 报告 + 回填"端到端。
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


# ---------------------------------------------------------------- 整理复制


def test_organize_copies_without_overwriting(tmp_path):
    src = tmp_path / "原始"
    src.mkdir()
    f = src / "MX.dna"
    f.write_bytes(b"hello")
    items = [{"file": {"path": f, "rel": Path("MX.dna"), "ext": ".dna", "stem": "mx"},
              "how": "测试"}]
    out = tmp_path / "out"
    manifest = []
    batch.organize("MX", items, out, manifest)
    assert (out / "MX" / "MX.dna").read_bytes() == b"hello"
    assert f.read_bytes() == b"hello"  # 原件未动
    assert manifest[0]["md5"] and manifest[0]["src"] == str(f)
    batch.organize("MX", items, out, manifest)  # 重跑不得覆盖，加序号
    assert (out / "MX" / "MX (2).dna").exists()
    assert (out / "MX" / "MX.dna").read_bytes() == b"hello"


# ---------------------------------------------------------------- 结论


def _res(variants, cds, coverage=100.0, reads=1, errors=None):
    return {
        "reads": [{"filename": "x.ab1"}] * reads,
        "errors": errors or [],
        "variants": variants,
        "consensus": {"coverage_percent": coverage},
        "cds_reports": cds,
    }


def test_excel_conclusion不合格_引用CDS主句():
    cds = [{"name": "MX", "coverage_status": "full", "protein_identical": False,
            "verdict": "CDS 已完整覆盖。MX 蛋白与设计不一致：5076 处的插入使阅读框移码，"
                       "翻译提前终止（产物 25 aa，设计为 600 aa）；另有 1 处确证的碱基替换"
                       "（5269 C>T）。另有 5 处低置信差异（最高 Q 25，疑似测序噪声）未计入判定"}]
    res = _res([{"confidence": "medium"}, {"confidence": "high"}, {"confidence": "low"}], cds)
    out = batch.excel_conclusion("MX", res, 3, True)
    assert out.startswith("不合格：[MX] MX 蛋白与设计不一致")
    assert "5269 C>T" in out and "低置信差异疑似测序噪声" in out


def test_excel_conclusion_编码区外差异():
    cds = [{"name": "MX", "coverage_status": "full", "protein_identical": True, "verdict": "x"}]
    v = {"confidence": "medium", "features": [{"name": "lac operator", "type": "other"}]}
    res = _res([v], cds, coverage=42.0)
    out = batch.excel_conclusion("MX", res, 1, True)
    assert out.startswith("合格：编码区蛋白与设计一致")
    assert "lac operator" in out and "测序覆盖 42%" in out


def test_excel_conclusion_完全一致与缺参考():
    res = _res([], [], reads=2)
    assert batch.excel_conclusion("MX", res, 2, True) == "合格：与设计一致"
    assert "缺少同名参考图谱" in batch.excel_conclusion("MX", None, 2, False)
    failed = _res([], [], reads=0, errors=[{"filename": "a.ab1", "error": "修剪后过短"}])
    assert batch.excel_conclusion("MX", failed, 1, True).startswith("分析失败：修剪后过短")


def test_main_sentence_extracts_middle():
    v = ("CDS 已完整覆盖。MX 蛋白与设计不一致：5076 处的插入使阅读框移码（产物 25 aa）。"
         "另有 5 处低置信差异（最高 Q 25，疑似测序噪声）未计入判定")
    assert batch.main_sentence(v) == "MX 蛋白与设计不一致：5076 处的插入使阅读框移码（产物 25 aa）"


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
    folder = out / "TST"
    # 原件未动 + 副本齐全 + 报告/JSON/清单/备份齐全
    assert (data_dir / "T1.ab1").exists()
    for name in ("TST.fasta", "T1.ab1", "测序分析报告.md", "分析结果.json"):
        assert (folder / name).exists(), name
    assert (out / "整理清单.csv").exists()
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
