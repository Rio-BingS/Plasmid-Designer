"""Verdict 出口契约（goal T2；A 5.1/5.2/5.5）

所有出口由 verdict.status 驱动，本契约把一致性钉死：

- 网页结论头符号（✓/✗/△）↔ Excel 一句话前缀 ↔ verdict.status 三方一致；
- 归档分拣目录（_conclusion_bucket）与 status 同域；
- status 只由结构化证据推导——改写文案（low_reason/detail 等）不得改变
  status（「文案改写不改 status」）；
- API `_summary` 下发 verdict/resolved_regions/unresolved_sites 等结构化键
  （B P1-8，M13）。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))
sys.path.insert(0, str(ROOT))

from core.sanger import batch  # noqa: E402
from core.sanger.verdict import build_verdict  # noqa: E402
from tests.sanger_scenarios import SCENARIOS  # noqa: E402

# status → (网页头符号, Excel 前缀, 分拣目录)；failed_reads 的「分析失败」
# 全失败形态（reads=0）在检查前单独跳过
STATUS_CONTRACT = {
    "pass": ("✓", "合格", "正确"),
    "fail": ("✗", "不合格", "错误"),
    "pending": ("△", "待定", "待定"),
    "mixed": ("△", "疑似混合", "混合"),
    "insufficient": ("△", "无法判定", "无法判定"),
    "failed_reads": ("△", "待定·读取失败", "读取失败"),
}


def _run_scenario(spec):
    from tests.test_sanger_scenarios import _run
    return _run(spec.id)


# 不经 analyze 的场景（构造目标在别的层）：显式登记跳过原因，不静默
NOT_VIA_ANALYZE = {
    "M5": "analyze 尚无 primer 参数（T17 引入）",
    "M8": "raw 级场景，测 abif_reader 峰窗（T24）",
    "M11": "app 层 _precheck 场景（T18）",
}


@pytest.mark.parametrize("spec", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_status_consistent_across_outlets(spec):
    """每个场景：网页符号 / Excel 前缀 / 分拣目录 / verdict.status 四方一致"""
    if spec.id in NOT_VIA_ANALYZE:
        pytest.skip(NOT_VIA_ANALYZE[spec.id])
    from app.sequencing_report import _conclusion_bucket
    _spec, res = _run_scenario(spec)
    vd = res["verdict"]
    status = vd["status"]
    assert status in STATUS_CONTRACT, f"未知 status {status}"
    sym, prefix, bucket = STATUS_CONTRACT[status]
    if sym is None:
        pytest.skip(f"{spec.id}: {status} 出口文案随 T4/T3 落地")
    if status == "failed_reads" and not res["reads"]:
        pytest.skip(f"{spec.id}: 全部 read 解析失败，出口为「分析失败」形态")
    conclusion = res["conclusion"]
    assert conclusion.startswith(sym), \
        f"{spec.id}: status={status} 应以 {sym} 开头，实际 {conclusion[:20]!r}"
    if status == "mixed" and any(
            c.get("protein_identical") is False for c in res["cds_reports"]):
        return  # 确证损伤让 ✗ 优先（fail > mixed），混合落正文
    excel = batch.excel_conclusion("P", res, len(res["reads"]), True)
    assert excel.startswith(prefix), \
        f"{spec.id}: status={status} Excel 应以 {prefix} 开头，实际 {excel[:24]!r}"
    assert _conclusion_bucket(excel) == bucket, \
        f"{spec.id}: status={status} 分拣应为 {bucket}，实际 {excel[:24]!r}"


def test_status_ignores_wording_changes():
    """文案改写不改 status：detail/low_reason 等文案字段任意改写，
    verdict.status 不动（status 只由结构化证据推导）"""
    spec = next(s for s in SCENARIOS if s.id == "S9")
    _spec, res = _run_scenario(spec)
    status0 = res["verdict"]["status"]
    for v in res["variants"]:
        v["low_reason"] = "随便改写的文案"
        v["reason_codes"] = ["made_up_code"]
    for u in res["unresolved_sites"]:
        u["detail"] = "随便改写的文案"
    vd2 = build_verdict(res, 1500)
    assert vd2["status"] == status0


def test_summary_payload_carries_structured_keys():
    """API `_summary` 下发 verdict/resolved_regions/unresolved_sites/
    mixed_corroboration/algo_version（B P1-8；旧记录缺省键回退）"""
    from app.routes.sequencing_routes import _summary
    spec = next(s for s in SCENARIOS if s.id == "S10")
    _spec, res = _run_scenario(spec)
    record = {
        **res,
        "analysis_id": "a1",
        "created_at": "2026-11-07T00:00:00",
        "reference": "A" * 1200,
        "features": [],
        "decomposed_alleles": {},
    }
    summary = _summary(record)
    for k in ("verdict", "resolved_regions", "unresolved_sites",
              "mixed_corroboration", "algo_version"):
        assert k in summary, k
    assert summary["verdict"]["status"] == "pending"
    # 旧记录（无新键）不炸、按缺省回退
    old = _summary({"reads": [], "variants": [], "consensus": {},
                    "coverage_ranges": [], "errors": [], "reference": "AC",
                    "features": [], "analysis_id": "x", "created_at": "",
                    "engine": "e", "conclusion": ""})
    assert old["verdict"] is None and old["resolved_regions"] == []
