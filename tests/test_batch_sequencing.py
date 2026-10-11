"""批量测序整理分析脚本测试（scripts/batch_sequencing_report.py）

覆盖：Excel 解析、文件名匹配（引物列/质粒名/未匹配）、整理落盘（同名异内容
不覆盖）、Excel 结论各分支、"信息表 + 图谱 + .ab1 → 质粒文件夹 + 报告 + 回填"
端到端，离线脚本与网页整理包同输入同产物（章节、按质粒/结论分置结构），
以及重跑同一输出目录时的旧副本清理、清单记录实际路径与 --dry-run 预览。
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


def _headings(md: str) -> list:
    return [ln for ln in md.splitlines() if ln.startswith("#")]


def test_offline_output_matches_web_package(tmp_path):
    """同一输入：离线脚本核心函数的落盘结果与网页整理包 ZIP 的章节标题、
    按质粒 / 正确·错误·无法判定 分置的目录结构完全一致（含双峰章节）"""
    import io
    import zipfile

    from app.routes import sequencing_routes
    from app.sequencing_report import ZIP_ROOT, build_batch_zip
    from tests.test_sequencing_batch import REF_MX, REF_MX2, _gb_mx, _mixed_ab1_mx

    data_dir = tmp_path / "results"
    data_dir.mkdir()
    (data_dir / "MX.gb").write_bytes(_gb_mx())
    (data_dir / "T1.ab1").write_bytes(_mixed_ab1_mx("T1.ab1")[1])      # 双峰 → 无法判定
    (data_dir / "PX.fasta").write_text(f">PX\n{REF_MX2}\n", encoding="utf-8")
    (data_dir / "P1.ab1").write_bytes(make_ab1(REF_MX2, [40] * len(REF_MX2)))  # → 正确
    (data_dir / "孤儿.ab1").write_bytes(make_ab1(REF_MX, [40] * len(REF_MX)))   # 未匹配
    xlsx = data_dir / "测序.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    ws.append(["MX", "T1", None])
    ws.append(["PX", "P1", None])
    wb.save(xlsx)
    _wb, _h, _cols, rows = batch.load_excel(xlsx)

    # 离线：扫描 → 核心函数 → 落盘
    out = tmp_path / "out"
    files = batch.scan_files(data_dir, out)
    batch.load_bytes(files)
    payload, entries = batch.run_offline_batch(files, rows, 20)
    batch.write_archive(entries, out)
    off_files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    off_dirs = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_dir()}

    # 网页：同样的文件条目走端点的 _run_batch + build_batch_zip
    web_entries = [{"name": f["name"], "ext": f["ext"], "stem": f["stem"],
                    "bytes": f["bytes"]} for f in files]
    reads = [e for e in web_entries if e["ext"] in batch.READ_EXTS]
    refs = [e for e in web_entries if e["ext"] not in batch.READ_EXTS]
    w_payload, groups, unmatched, records = sequencing_routes._run_batch(
        reads, refs, rows, [], 20)
    zf = zipfile.ZipFile(io.BytesIO(build_batch_zip(
        w_payload, groups, unmatched, None, None, None, records)))
    prefix = f"{ZIP_ROOT}/"
    web_names = [n[len(prefix):] for n in zf.namelist()]
    web_files = {n for n in web_names if not n.endswith("/")}
    web_dirs = {n.rstrip("/") for n in web_names if n.endswith("/")}
    web_dirs |= {"/".join(n.split("/")[:i]) for n in web_files
                 for i in range(1, n.count("/") + 1)}

    assert off_files == web_files
    assert off_dirs == web_dirs
    assert {"MX/MX.gb", "MX/无法判定/T1.ab1", "MX/无法判定/测序分析报告.md",
            "PX/PX.fasta", "PX/正确/P1.ab1", "PX/正确/测序分析报告.md",
            "未匹配文件/孤儿.ab1", "整理清单.csv", "批次总览.txt"} <= off_files
    assert {"MX/正确", "MX/错误", "PX/正确", "PX/错误"} <= off_dirs

    # 结论一致；每份报告的章节标题一致，混合样品报告含双峰章节
    assert [it["conclusion"] for it in payload["items"]] == \
        [it["conclusion"] for it in w_payload["items"]]
    reports = sorted(n for n in off_files if n.endswith(".md"))
    assert len(reports) == 2
    for name in reports:
        off_md = (out / name).read_text(encoding="utf-8")
        web_md = zf.read(prefix + name).decode("utf-8")
        assert _headings(off_md) == _headings(web_md), name
    mx_heads = _headings((out / "MX/无法判定/测序分析报告.md").read_text(encoding="utf-8"))
    assert "## 双峰（疑似混合）检测" in mx_heads


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
