"""结论正文 6 行预算（conclusion_fold）与头行范围段的定向单测（2026-11-18）。

- ⚠ 级混合汇总行永不折叠：此前排第 4 级，poly 大量缺失等置顶要点占满名额后会被
  折进 mixed_detail（KNOWN_ISSUES 未解决第 9 条，形态实测于本机真实基准，
  不入仓库）。
- 差异摘要行只在需要给 ⚠ 汇总行腾位、且挤掉就能回到预算内时才折叠。
- 头行范围段：read 全部失败时不再拼出「已测 ；范围外未评估」（未解决第 8 条）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.sanger import conclusion_fold as cf  # noqa: E402

HEAD = "✗ 不合格 · 已测 1–428；范围外未评估 · 证据：1 处确证差异"
POLY = "⚠ poly 大量缺失：poly(A) 5201–5307 大量缺失 39 个"
SUMMARY = "共检出 12 处差异（覆盖 63.2%）："
WARN1 = "⚠ 测序套峰：r1.ab1 自 read 第 169 位起整体套峰"
DET1 = "  ↳ r1.ab1：双峰 227 处中 215 处无其他引物覆盖待核"
WARN2 = "⚠ 2 处参考位点被多条引物同报双峰——建议重新挑单克隆复测"
LOW = "位置 65 G>A（非编码区），未决"
CONF = "位置 5201 缺失 38bp 导致移码"
CDS = "【CDS】CDS 已完整覆盖。蛋白与设计一致"
NOTE = "注意：共 1 段未覆盖缺口"


def _run(lines, mixed, summary=(SUMMARY,), low=(LOW,)):
    groups = {"low": list(low), "mixed_detail": list(mixed), "resolved": []}
    out = cf.apply_budget("\n".join(lines), groups, mixed_lines=mixed,
                          summary_lines=summary)
    return out.split("\n"), groups


def test_warn_mixed_summary_kept_when_key_lines_fill_budget():
    """真实基准形态（本机）：⚠ 汇总行两条都留在正文，挤掉的是差异摘要行，正文 ≤6 行"""
    body, groups = _run([HEAD, POLY, SUMMARY, WARN1, DET1, WARN2, LOW, CONF, CDS, NOTE],
                        mixed=[WARN1, DET1, WARN2])
    assert WARN1 in body and WARN2 in body
    assert SUMMARY not in body and SUMMARY in groups["notes"]
    assert DET1 not in body and LOW not in body and NOTE not in body
    assert body == [HEAD, POLY, WARN1, WARN2, CONF, CDS]
    assert len(body) <= cf.CONCLUSION_BUDGET


def test_summary_kept_when_no_warn_line():
    """没有 ⚠ 混合汇总行时差异摘要行照旧不折叠（要点超预算按实数展示）"""
    lines = [HEAD, POLY, SUMMARY, CONF, "位置 10 A>T 错义", "位置 20 C>G 错义", CDS, NOTE]
    body, groups = _run(lines, mixed=[])
    assert SUMMARY in body and NOTE not in body
    assert len(body) == 7


def test_summary_kept_when_eviction_cannot_reach_budget():
    """挤掉差异摘要行也回不到预算内时不动它，⚠ 汇总行照样留在正文"""
    extra = [f"位置 {i} A>T 错义" for i in range(1, 6)]
    body, _ = _run([HEAD, SUMMARY, WARN1] + extra + [CDS], mixed=[WARN1])
    assert SUMMARY in body and WARN1 in body


def test_non_warn_mixed_summary_still_foldable():
    """非 ⚠ 级的混合行仍按第 4 级折叠"""
    soft = "零散双峰 3 处（可能为噪声）"
    body, groups = _run([HEAD, SUMMARY, soft, CONF, "a", "b", CDS], mixed=[soft])
    assert soft not in body and soft in groups["mixed_detail"]
    assert SUMMARY in body


def test_under_budget_untouched():
    text = "\n".join([HEAD, WARN1, CDS])
    assert cf.apply_budget(text, {}, mixed_lines=[WARN1], summary_lines=[]) == text


def test_range_text_no_spans_says_no_usable_region():
    """read 全部失败（无 verified、无实测区段）→「无可用测序区段」，不再留空"""
    from core.sanger.verdict import _range_text
    assert _range_text(None, [], 7079, []) == "无可用测序区段"
    assert _range_text(None, [(1, 100)], 7079, []) == "已测 1–100；范围外未评估"
