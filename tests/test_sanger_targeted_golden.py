"""定向金标准（2026-11-16）：293 例样本里没出现过的文案分支，冻结 v17 措辞。

- 低置信原因：互检矛盾、合并压缩区、滑移、同聚物压缩、插入峰偏弱；
- 双峰：「自身信号问题」行（noise_like + 连续双峰段）；
- Excel：poly 尾注只在旧记录（无 verdict）降级路径出现——新记录结论列逐字取
  verdict 头行，所以 293 例里 poly 尾注计数为 0 是预期行为。
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "backend"))

import pytest  # noqa: E402

from core.sanger.evidence import low_confidence_cause  # noqa: E402
from core.sanger import batch, render_mixed, render_site  # noqa: E402
from core.sanger.crosscheck import mixed_summary  # noqa: E402

_B = {"read_q": 18, "read_pos": 200, "support_reads": 1, "type": "substitution", "alt_base": "A"}


@pytest.mark.parametrize("v,ev,text", [
    (dict(_B, cross_read_conflict={"reads": ["r1", "r2", "r3"]}), None,
     "互检矛盾：r1、r2 在同一位点读出设计碱基"),
    (dict(_B, merged_zone_artifact=True), None,
     "落在同聚物峰合并压缩区，该 read 此处无可分辨峰"),
    (dict(_B, slippage_artifact=True), None, "poly 下游滑移伪影（同聚物后信号错位）"),
    (dict(_B, type="deletion", homopolymer={"peak_amplitude_ratio": 0.31}), None,
     "同聚物峰压缩（峰幅为局部主峰的 31%），重复数计数不可靠"),
    (dict(_B, type="insertion"), {"insertion_peak_ratio": 0.45},
     "插入峰偏弱（为邻峰的 45%）且 Q 值低，Q18"),
])
def test_rare_low_reasons(v, ev, text):
    c = low_confidence_cause(v, set(), ev)
    assert render_site.low_reason_text({**v, "low_cause": c}) == text


def _rz():
    return {"start": 30, "end": 700, "raw_start": 25, "raw_end": 700}


def test_self_signal_line():
    rr = [{"filename": "a.ab1", "reliable_zone": _rz(),
           "mixed_profile": {"class": "widespread", "count": 6, "minor_fraction": 0.2,
                             "longest_stretch": 9, "span": [120, 140], "positions": [10, 11]}},
          {"filename": "b.ab1", "reliable_zone": _rz(),
           "mixed_profile": {"class": "none", "count": 0, "minor_fraction": 0,
                             "longest_stretch": 0, "span": None, "positions": []}}]
    corr = {"by_read": {"a.ab1": {"multi": 0, "clean": 4, "uncovered": 0, "uncovered_ref": []}},
            "total": 6, "multi_sites": []}
    assert render_mixed.read_lines(mixed_summary(rr, corr)) == [
        "⚠ a.ab1 检出 6 处双峰位点（估计次要克隆占比约 20%）——跨引物互检：多数双峰位点被"
        "其他引物测过且峰形单一、判读一致，更倾向该 read 自身信号问题（信号不稳区以引物首端为主）"
        "而非真实混合，建议核对峰图与互检位置后复测该段；自位置 120 起存在连续双峰段"
        "（若互检未覆盖该段，需警惕两个克隆相差插入/缺失）"]


@pytest.mark.parametrize("state,tail", [
    ("CONFLICTING", "poly 区计数互证矛盾"),
    ("UNDETERMINABLE", "poly 区计数不可判定"),
    ("DEFICIT_SUSPECTED", "poly 区计数存疑"),
])
def test_excel_poly_tail_legacy_path(state, tail):
    res = {"reads": [{"filename": "x.ab1"}], "errors": [], "variants": [],
           "consensus": {"coverage_percent": 100.0}, "cds_reports": [],
           "poly_calls": [{"tier": "poly", "state": state}]}
    assert tail in batch.excel_conclusion("MX", res, 1, True)
    # 有 verdict 时结论列取头行，不追加 poly 尾注
    res["verdict"] = {"headline": "✓ 合格：与设计一致"}
    assert tail not in batch.excel_conclusion("MX", res, 1, True)
