"""复审（2026-11-08）修复红测：假 ✓ 三处 + 首端连接段 + verified 口径。

对应复审条目（全部已对代码取证）。2026-11-09 goal v3 重编码留痕：
P0-1/P0-2 的「整质粒 partial / cds_union 覆盖闸」语义被 goal v3 §8 决策 1
取代——未声明范围 → 已测区段口径 pass（不判 partial、不出「构建与设计
一致」），覆盖缺口只对声明范围判定；本文件相应改为锁定 v3 语义（未声明
口径 + 声明范围下的 pending 闸各留一锁）：
- P0-1 整质粒模式部分覆盖：未声明范围禁「构建与设计一致」；声明范围下
  关键元件缺口 → critical_element_unverified pending。
- P0-2 verified 口径：声明范围下实测齐全但 verified<100% → pending
  （pass ⇒ blocking 为空契约由 test_sanger_verdict_contract 钉死）。
- P0-3 AF 15-30% 混合在 CDS 内静默 ✓：ratio_floor=0.30 硬地板使
  MINOR_AF_MIN=0.15 成死代码 → 该带应检出（AF≥0.23 判 mixed）。
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


def test_whole_roi_partial_measured_scope_wording():
    """P0-1（v3 §8 决策 1 重编码）：整质粒只测 1-900/3000 → 未声明范围
    「已测区段」口径 pass，头行/Excel 不得主张「构建与设计一致」；
    声明范围后缺口判定随范围走：声明 1-900（实测齐全但首端 30bp 端部
    软剪切不可信）→ pending + roi_unverified；声明 1-2200（实测只有
    40.9%）→ partial + roi_uncovered（覆盖闸 if/elif 链先判 uncovered）"""
    rng = _rng("rr1")
    ref = _rand_seq(rng, 3000)
    blob = _read_from_ref(ref, 1, 900, q=50)
    res = analyze([("r.ab1", blob)], ref, [], circular=False)
    assert _status(res) == "pass"
    assert res["verdict"]["scope"]["declared"] is False
    assert "构建与设计一致" not in res["conclusion"]
    res2 = analyze([("r.ab1", blob)], ref, [], circular=False, scope="1-900")
    assert _status(res2) == "pending"
    assert "roi_unverified" in _blocking_kinds(res2)
    res3 = analyze([("r.ab1", blob)], ref, [], circular=False, scope="1-2200")
    assert _status(res3) == "partial"
    # W6 起 roi_uncovered 按 E 归因拆分：read 只测 1-900，901-2200 超出
    # 理论覆盖 → beyond_primer（加引物），语义仍是「实测缺口 → partial」
    assert "scope_beyond_primer" in _blocking_kinds(res3)


def test_critical_element_unverified_pending():
    """P0-1（v3 重编码）：启动子 2000-2200 完全没测到 → 未声明范围不阻断
    （范围外未评估）；声明范围覆盖到启动子时缺口 → pending 阻断，
    不得静默 info"""
    rng = _rng("rr2")
    ref = _rand_seq(rng, 3000)
    blob = _read_from_ref(ref, 1, 900, q=50)
    feats = [dict(name="CMV promoter", type="promoter",
                  start=2000, end=2200, strand="+")]
    res = analyze([("r.ab1", blob)], ref, feats, circular=False)
    assert "critical_element_unverified" not in _blocking_kinds(res)
    assert _status(res) == "pass"
    res2 = analyze([("r.ab1", blob)], ref, feats, circular=False,
                   scope="1-2200")
    assert "critical_element_unverified" in _blocking_kinds(res2)
    assert _status(res2) == "pending"


def test_cds_verified_gap_pending_not_pass():
    """P0-2：CDS 实测齐全但 verified<100%（首端硬区在 CDS 内）→ 不得
    ✓+blocking 矛盾，须 pending"""
    rng = _rng("rr3")
    ref = list(_rand_seq(rng, 1500))
    ref[0:900] = list(_clean_orf(rng, 300))   # CDS 1-900，read 1-1000 全覆盖
    ref = "".join(ref)
    blob = _read_from_ref(ref, 1, 1000, q=50)
    feats = [dict(name="GeneX", type="CDS", start=1, end=900, strand="+")]
    res = analyze([("r.ab1", blob)], ref, feats, circular=False, scope="1-900")
    assert _status(res) == "pending"
    assert "roi_unverified" in _blocking_kinds(res)


def test_low_af_mixed_in_cds_detected():
    """P0-3（goal v3 W4 重编码，2026-11-09）：CDS 内次级/主峰面积比 0.22
    （→ AF≈0.18）双峰 → 不得静默 ✓；落在 W4 分带 [0.15, 0.23) →
    minor_af_site pending 复核项（判混须 AF≥0.23 或多位点互证，
    「静默 ✓」与「直接判混」都被禁止）"""
    rng = _rng("rr4")
    ref = _rand_seq(rng, 1200)
    main = ref[449]
    alt = next(b for b in "ACGT" if b != main)
    blob = _read_from_ref(ref, 1, 1000,
                          secondary={450: (alt, 0.22)}, q=50)
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    res = analyze([("mix.ab1", blob)], ref, feats, circular=False)
    assert _status(res) == "pending"
    assert _status(res) != "pass"
    assert "minor_af_site" in _blocking_kinds(res)


def test_synonymous_substitution_configurable():
    """goal v3 §8 决策 3（W5）：CDS 内确证同义替换默认「合格（注明同义）」
    ——verdict pass + synonymous_variant info + Excel 合格注明；显式
    synonymous_action="fail" 沿旧口径按确证差异阻断（可配置项锁双向）"""
    rng = _rng("rr_syn")
    ref = list(_rand_seq(rng, 1200))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(98)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    cds = list("".join(codons))
    cds[3:6] = "CTT"                       # Leu
    mut = list(cds)
    mut[5] = "A"                           # CTT→CTA，仍 Leu（同义）
    ref[300:600] = cds
    ref = "".join(ref)
    mut_full = list(ref)
    mut_full[305] = mut[5]                 # 参考坐标 306
    blob_f = make_trace_ab1("".join(mut_full)[200:1000], q=50)
    blob_r = make_trace_ab1("".join(mut_full)[200:1000], q=50)
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    res = analyze([("f.ab1", blob_f), ("r.ab1", blob_r)], ref, feats,
                  scope="GeneX", circular=False)
    vd = res["verdict"]
    assert _status(res) == "pass"
    assert vd["blocking"] == []
    assert any(i.get("kind") == "synonymous_variant" for i in vd["info"])
    assert "同义" in vd["headline"]
    from core.sanger.batch import excel_conclusion
    assert "同义" in excel_conclusion("P", res, 2, True)
    # 可配置反向：按旧口径判确证差异
    res2 = analyze([("f.ab1", blob_f), ("r.ab1", blob_r)], ref, feats,
                   scope="GeneX", circular=False, synonymous_action="fail")
    assert _status(res2) == "fail"
    assert "confirmed_variant" in _blocking_kinds(res2)
    """P1-6：首端 20bp 高质量外源段（错误连接，落在 front_hard 豁免区）
    → 不得静默：出 end_junction_unverified 待核阻断（split-read 定位到
    断点仍按 structural_breakpoint fail）"""
    rng = _rng("rr5")
    ref = _rand_seq(rng, 1500)
    wrong = "".join(rng.choice("ACGT") for _ in range(20))
    blob = make_trace_ab1(wrong + ref[0:880], q=50)
    res = analyze([("r.ab1", blob)], ref, [], circular=False)
    kinds = _blocking_kinds(res)
    assert "end_junction_unverified" in kinds
    assert _status(res) == "pending"


def test_conclusion_groups_structured():
    """R6/P1-7：结论折叠组由后端结构化下发（low/mixed_detail/resolved），
    前端不再靠「低置信度（/↳+双峰」文案匹配分组"""
    rng = _rng("rr6")
    ref = _rand_seq(rng, 1200)
    main = ref[449]
    alt = next(b for b in "ACGT" if b != main)
    blob = _read_from_ref(ref, 1, 1000, subs=[(950, "G" if ref[949] != "G" else "T")],
                          secondary={450: (alt, 0.4)}, q=50)
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    res = analyze([("mix.ab1", blob)], ref, feats, circular=False)
    groups = res.get("conclusion_groups")
    assert groups is not None
    assert set(groups) == {"low", "mixed_detail", "resolved"}
    concl_lines = set(res["conclusion"].split("\n"))
    # 组内每一行都必须逐字来自结论文本（分组是行的子集标注，不是再加工）
    for kind in ("low", "mixed_detail", "resolved"):
        for line in groups[kind]:
            assert line in concl_lines, (kind, line)
    # 混合位点在 CDS 内 → mixed_detail 组含逐 read 双峰行；低置信行归 low 组
    blob2 = _read_from_ref(ref, 1, 700, subs=[(20, "G" if ref[19] != "G" else "T")], q=40)
    res2 = analyze([("r2.ab1", blob2)], ref, feats, circular=False)
    g2 = res2["conclusion_groups"]
    assert g2["low"], "首端硬区低置信变异应进 low 组"
    assert any("低置信度" in l for l in g2["low"])
