"""复审（2026-11-08）修复红测：假 ✓ 三处 + 首端连接段 + verified 口径。

对应复审条目（全部已对代码取证）：
- P0-1 整质粒模式部分覆盖仍 ✓：whole ROI 实测 <99% 应 partial（不能只
  记 info）；关键元件（ori/启动子）未验证应 pending 阻断，不能只写 info。
- P0-2 verified 口径：cds_union 实测齐全但 verified<100% 时挂 blocking
  roi_unverified 却仍 ✓（头行与阻断列表自相矛盾）→ 应 pending。
- P0-3 AF 15-30% 混合在 CDS 内静默 ✓：ratio_floor=0.30 硬地板使
  MINOR_AF_MIN=0.15 成死代码 → 该带应检出并升级 mixed_variant。
- P1-6 首端 ≤front_hard 高质量剪切段（错误连接）被无条件豁免 → 应上报。

本文件断言「修复后的最终期望」；先红后绿（无 xfail——修复提交即转绿）。
"""

import pytest

from core.sanger.pipeline import analyze
from tests.abif_utils import make_trace_ab1
from tests.sanger_scenarios import _rand_seq, _rng, _read_from_ref


def _status(res):
    return (res.get("verdict") or {}).get("status")


def _blocking_kinds(res):
    return {b.get("kind") for b in (res.get("verdict") or {}).get("blocking", [])}


def _clean_orf(rng, n_codons):
    """ATG..TAA 的干净 ORF（避开 orf_annotation_suspect 护栏，见 S8）"""
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(n_codons - 2)] + ["TAA"]
    return "".join(c if c not in ("TAA", "TAG", "TGA") else "GCT"
                   for c in codons)


def test_whole_roi_partial_measured_never_pass():
    """P0-1：整质粒模式只测 1-900/3000 → 不得 ✓，须 partial + roi_uncovered"""
    rng = _rng("rr1")
    ref = _rand_seq(rng, 3000)
    blob = _read_from_ref(ref, 1, 900, q=50)
    res = analyze([("r.ab1", blob)], ref, [], circular=False)
    assert _status(res) == "partial"
    assert "roi_uncovered" in _blocking_kinds(res)


def test_critical_element_unverified_pending():
    """P0-1：启动子 2000-2200 完全没测到 → 不得静默 info，须 pending 阻断"""
    rng = _rng("rr2")
    ref = _rand_seq(rng, 3000)
    blob = _read_from_ref(ref, 1, 900, q=50)
    feats = [dict(name="CMV promoter", type="promoter",
                  start=2000, end=2200, strand="+")]
    res = analyze([("r.ab1", blob)], ref, feats, circular=False)
    assert "critical_element_unverified" in _blocking_kinds(res)
    assert _status(res) == "pending"


def test_cds_verified_gap_pending_not_pass():
    """P0-2：CDS 实测齐全但 verified<100%（首端硬区在 CDS 内）→ 不得
    ✓+blocking 矛盾，须 pending"""
    rng = _rng("rr3")
    ref = list(_rand_seq(rng, 1500))
    ref[0:900] = list(_clean_orf(rng, 300))   # CDS 1-900，read 1-1000 全覆盖
    ref = "".join(ref)
    blob = _read_from_ref(ref, 1, 1000, q=50)
    feats = [dict(name="GeneX", type="CDS", start=1, end=900, strand="+")]
    res = analyze([("r.ab1", blob)], ref, feats, circular=False)
    assert _status(res) == "pending"
    assert "roi_unverified" in _blocking_kinds(res)


def test_low_af_mixed_in_cds_detected():
    """P0-3：CDS 内 AF≈0.22 双峰 → 不得静默 ✓，须检出并升级 mixed_variant"""
    rng = _rng("rr4")
    ref = _rand_seq(rng, 1200)
    main = ref[449]
    alt = next(b for b in "ACGT" if b != main)
    blob = _read_from_ref(ref, 1, 1000,
                          secondary={450: (alt, 0.22)}, q=50)
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    res = analyze([("mix.ab1", blob)], ref, feats, circular=False)
    assert _status(res) == "mixed"
    assert "mixed_variant" in _blocking_kinds(res)


def test_front_short_junction_reported():
    """P1-6：首端 20bp 高质量外源段（错误连接，落在 front_hard 豁免区）
    → 不得静默，须进 blocking"""
    rng = _rng("rr5")
    ref = _rand_seq(rng, 1500)
    wrong = "".join(rng.choice("ACGT") for _ in range(20))
    blob = make_trace_ab1(wrong + ref[0:880], q=50)
    res = analyze([("r.ab1", blob)], ref, [], circular=False)
    kinds = _blocking_kinds(res)
    assert "unaligned_hq_segment" in kinds
